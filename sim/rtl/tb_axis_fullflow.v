//=============================================================================
// tb_fullflow.v —— 全流程仿真（PL + PS 行为）
//=============================================================================
// 把整条链跑起来：
//   Cyton 字节流 → uart_rx → pkt_parser → dc_block → biquad_bank
//                → csp_project → win_var → lognorm → svm_core → DECISION
//
// 同时用一个 **AXI-Lite 主设备 BFM 模拟 PS 的行为**：
//   ① 按 eeg_regs.h 的流程下发参数（PADDR → PRAM → PCOMMIT → 查 CRC 位）
//   ② 轮询 ACC_CNT，每来一个新决策就读 DECISION
//
// ★ 为什么用 BFM 而不是真跑 PS 的 C 代码
//   仿真环境里没有 Linux/ARM；但 PS 的行为**只是一串寄存器读写**，
//   用 BFM 复现这段时序就能验证接口契约（寄存器偏移、位域、握手、CRC 流程）。
//   PS 侧的算法（EA 折叠）已由 zynq/ps/verify_ea_math.py 独立验证。
//   两者合起来覆盖了"PL 逻辑 + PS 接口 + PS 算法"。
//
// 激励：tools/sample_stream.bin（744 包），由 verify_fullflow.py 转成十进制
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module tb_axis_fullflow;

`ifndef N_BYTE
    `define N_BYTE 24552
`endif
`ifndef N_PKT
    `define N_PKT  744
`endif

    localparam integer N_BYTE = `N_BYTE;
    localparam integer N_PKT  = `N_PKT;

    localparam integer CLK_FREQ = 1000;      // 与 tb_cosim 同样的等比缩小
    localparam integer BAUD     = 100;
    localparam integer DIV      = CLK_FREQ / BAUD;
    localparam real    CLK_NS   = 10.0;
    localparam real    BIT_NS   = DIV * CLK_NS;   // 100 ns/位

    localparam integer N_CH = 3, N_BAND = 3, N_COMP = 2, FEAT_DIM = 4;
    localparam integer WIN_LEN = 626;

    // ★ 顶层现在自带 PLL：外部只给 100 MHz 的 clk_in 与一个外部复位，
    //   95 MHz 与系统复位都在 PLL 内部生成（locked → 复位同步器）。
    reg clk = 0, rst_n_ext = 0, rx = 1;
    always #5 clk = ~clk;

    // ── AXI-Lite 主设备信号 ──
    reg  [31:0] awaddr = 0, wdata = 0, araddr = 0;
    reg         awvalid = 0, wvalid = 0, bready = 0, arvalid = 0, rready = 0;
    reg  [3:0]  wstrb = 4'hF;
    wire        awready, wready, bvalid, arready, rvalid;
    wire [1:0]  bresp, rresp;
    wire [31:0] rdata;
    wire        led_pkt, led_label, dec_valid;

    eeg_csp_svm_top #(
        .CLK_FREQ(CLK_FREQ), .BAUD(BAUD),
        .N_CH(N_CH), .N_BAND(N_BAND), .N_COMP(N_COMP),
        .FEAT_DIM(FEAT_DIM), .WIN_LEN(WIN_LEN)
    ) dut (
        .clk_in(clk), .rst_n_ext(rst_n_ext), .uart_rx(rx), .uart_tx(),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid),
        .s_axi_wready(wready), .s_axi_bresp(bresp), .s_axi_bvalid(bvalid),
        .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid),
        .s_axi_rready(rready),
        .led_pkt(led_pkt), .led_label(led_label), .dec_valid(dec_valid));

    // ══════════════════════════════════════════════════════════
    //  AXI-Lite 主设备 BFM（模拟 PS）
    // ══════════════════════════════════════════════════════════
    task axi_write(input [31:0] addr, input [31:0] data);
        begin
            @(posedge clk);
            awaddr  <= addr;  awvalid <= 1'b1;
            wdata   <= data;  wvalid  <= 1'b1;  wstrb <= 4'hF;
            bready  <= 1'b1;
            @(posedge clk);
            while (!(awready && wready)) @(posedge clk);
            awvalid <= 1'b0;  wvalid <= 1'b0;
            while (!bvalid) @(posedge clk);
            @(posedge clk);
            bready <= 1'b0;
        end
    endtask

    task axi_read(input [31:0] addr, output [31:0] data);
        begin
            @(posedge clk);
            araddr <= addr;  arvalid <= 1'b1;  rready <= 1'b1;
            @(posedge clk);
            while (!arready) @(posedge clk);
            arvalid <= 1'b0;
            while (!rvalid) @(posedge clk);
            data = rdata;
            @(posedge clk);
            rready <= 1'b0;
        end
    endtask

    // ══════════════════════════════════════════════════════════
    //  激励与参数
    // ══════════════════════════════════════════════════════════
    reg [7:0]  bytes  [0:N_BYTE-1];
    reg [31:0] sos_p  [0:29];        // 3 带 × 2 节 × 5 系数
    reg [31:0] csp_p  [0:N_BAND*N_CH*N_COMP-1];
    reg [31:0] svmw_p [0:FEAT_DIM-1];
    reg [31:0] svmb_p;

    integer fd, code, i, n, k;
    integer n_dec_seen, last_acc;

    // ── 参数下发（复现 eeg_ps.c 的流程）──
    task push_block(input [7:0] blk, input integer cnt, input integer base_idx);
        integer t;
        begin
            axi_write(32'h2C, {blk, 8'h00, cnt[15:0]});
            for (t = 0; t < cnt; t = t + 1) begin
                case (blk)
                    8'h01: axi_write(32'h30, sos_p[base_idx + t]);
                    8'h03: axi_write(32'h30, csp_p[base_idx + t]);
                    8'h04: axi_write(32'h30, svmw_p[base_idx + t]);
                    8'h05: axi_write(32'h30, svmb_p);
                    default: ;
                endcase
            end
            axi_write(32'h34, 32'd1);            // PARAM_COMMIT
        end
    endtask

    integer st;
    integer fd_dec;

    // ★ 决策监视：直接观察 dec_valid，不经过 AXI 轮询。
    //   第一版把 AXI 轮询和喂数放在 fork...join 里并发跑，
    //   结果只收到 1 个包 —— "算法对不对"和"AXI 接口通不通"耦合在一起，
    //   出问题时无法区分是哪一边。拆开后两边各自可独立定位。
    integer mon_cnt = 0;
    integer fd_f, fd_z, fd_sz;
    always @(posedge clk) begin
        if (dut.rst_n && dec_valid) begin
            $fdisplay(fd_dec, "%0d %0d", dut.dec_label, dut.dec_value);
            $fdisplay(fd_f, "%0d %0d %0d %0d",
                      dut.f_sel[0], dut.f_sel[1], dut.f_sel[2], dut.f_sel[3]);
            $fdisplay(fd_z, "%0d %0d %0d %0d %0d %0d",
                      dut.z[0], dut.z[1], dut.z[2], dut.z[3], dut.z[4], dut.z[5]);
            $fdisplay(fd_sz, "%0d %0d %0d %0d %0d %0d",
                      dut.sz[0], dut.sz[1], dut.sz[2], dut.sz[3], dut.sz[4], dut.sz[5]);
            mon_cnt = mon_cnt + 1;
        end
    end

    `include "axis_fullflow_checks.vh"

    initial begin
        // ── 读激励 ──
        fd = $fopen("ff_bytes.txt", "r");
        if (fd == 0) begin $display("!! 缺 ff_bytes.txt"); $finish; end
        for (i = 0; i < N_BYTE; i = i + 1) code = $fscanf(fd, "%d\n", bytes[i]);
        $fclose(fd);

        fd = $fopen("ff_sos.txt", "r");
        for (i = 0; i < 30; i = i + 1) code = $fscanf(fd, "%d\n", sos_p[i]);
        $fclose(fd);
        fd = $fopen("ff_csp.txt", "r");
        for (i = 0; i < N_BAND*N_CH*N_COMP; i = i + 1) code = $fscanf(fd, "%d\n", csp_p[i]);
        $fclose(fd);
        fd = $fopen("ff_svmw.txt", "r");
        for (i = 0; i < FEAT_DIM; i = i + 1) code = $fscanf(fd, "%d\n", svmw_p[i]);
        $fclose(fd);
        fd = $fopen("ff_svmb.txt", "r");
        code = $fscanf(fd, "%d\n", svmb_p);
        $fclose(fd);

        fd_dec = $fopen("ff_dec.txt", "w");
        fd_f   = $fopen("ff_f.txt",   "w");
        fd_z   = $fopen("ff_z.txt",   "w");
        fd_sz  = $fopen("ff_sz.txt",  "w");

        // ── 复位 ──
        // PLL 需要一点时间锁定（行为模型里是 16 拍），
        // 复位同步器再花 4 拍释放，所以这里要多等一会儿。
        rst_n_ext = 0; #200; rst_n_ext = 1; #500;

        // ══ PS 行为①：下发参数 ══
        $display("[ff] PS: 下发 IIR_SOS（30 字）...");
        push_block(8'h01, 30, 0);
        $display("[ff] PS: 下发 CSP_W（%0d 字，已含 EA 折叠，每带一组）...",
                 N_BAND*N_CH*N_COMP);
        push_block(8'h03, N_BAND*N_CH*N_COMP, 0);
        $display("[ff] PS: 下发 SVM_W（%0d 字）+ SVM_B ...", FEAT_DIM);
        push_block(8'h04, FEAT_DIM, 0);
        push_block(8'h05, 1, 0);

        axi_read(32'h04, st);
        $display("[ff] PS: STATUS=0x%08X  params_loaded=%b crc_ok=%b",
                 st, dut.params_loaded, st[2]);

        axi_write(32'h00, 32'h1);            // CTRL.start = 1
        axis_prepare();

        // ══ 数据流：按真实串口时序喂 Cyton 字节（串行，不并发）══
        $display("[ff] PL: 开始接收 %0d 字节（%0d 包）...", N_BYTE, N_PKT);
        for (n = 0; n < N_BYTE; n = n + 1) begin
            rx = 1'b0; #(BIT_NS);
            for (k = 0; k < 8; k = k + 1) begin
                rx = bytes[n][k]; #(BIT_NS);
            end
            rx = 1'b1; #(BIT_NS);
        end
        $display("[ff] 字节发送完毕，等待决策流水线跑完 ...");
        #(BIT_NS * 400);

        // ══ PS 行为②：读回状态（验证 AXI 读通道）══
        axi_read(32'h24, st);
        $display("[ff] PS: ACC_CNT=%0d（应为 %0d）", st, mon_cnt);
        axi_read(32'h18, k);
        $display("[ff] PS: DECISION=0x%08X (label=%0d)", k, k & 1);
        axi_read(32'h04, st);
        $display("[ff] PS: STATUS=0x%08X", st);

        #(BIT_NS * 50);
        axis_finish();
        $fclose(fd_dec); $fclose(fd_f); $fclose(fd_z); $fclose(fd_sz);
        $display("[ff] 完成: cnt_pkt=%0d  决策数=%0d", dut.cnt_pkt, mon_cnt);
        $display("[ff] 错误: footer=%0d sync=%0d drop=%0d frame=%0d",
                 dut.cnt_footer, dut.cnt_sync, dut.cnt_drop, dut.cnt_frame);
        #100; $finish;
    end

    initial begin
        #(BIT_NS * N_BYTE * 12 + 100000);
        $fatal(1,"Fullflow watchdog timeout: packets=%0d decisions=%0d",dut.cnt_pkt,mon_cnt);
    end

endmodule

`default_nettype wire
