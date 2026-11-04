//=============================================================================
// tb_pkt_parser.v —— uart_rx + pkt_parser 级联集成测试
//=============================================================================
// 验证项：
//   T1 合法 33 字节包 → 8 通道数据正确（含负数）
//   T2a 纯垃圾前缀（不含 0x41）→ 立即同步
//   T2b 假 BOP → 靠包尾校验拒绝，下一包恢复
//   T3 包尾非法 → cnt_footer_err 递增，且不输出样本
//   T4 包内字节间隔超时 → cnt_sync_err 递增，随后能恢复
//   T5 序号跳变 → cnt_drop 正确累加
//   T6 连续 3 包 → cnt_pkt 正确
//
// ★ 测试台设计注意：cap_* 只能由 always 块驱动。
//   第一版让 initial 块也写 cap_valid，形成多驱动竞争，
//   导致 T2b 之后的用例随机失败。改用**样本计数器增量**做判据。
//
// 运行：iverilog -g2012 -o tb_pkt.vvp tb_pkt_parser.v ../rtl/uart_rx.v ../rtl/pkt_parser.v && vvp tb_pkt.vvp
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module tb_pkt_parser;

    localparam integer CLK_FREQ = 100_000_000;
    localparam integer BAUD     = 115200;
    localparam integer DIV      = CLK_FREQ / BAUD;
    localparam real    BIT_NS   = 1e9 * DIV / CLK_FREQ;   // 8680 ns
    localparam real    BYTE_NS  = BIT_NS * 10;            // 86.8 us

    reg  clk = 0, rst_n = 0, rx = 1;
    wire [7:0] byte_out;
    wire       byte_valid, frame_err;

    wire               sample_valid;
    wire signed [23:0] ch_data [0:7];
    wire [7:0]         sample_seq;
    wire [3:0]         pkt_type;
    wire [7:0]         cnt_footer_err, cnt_sync_err, cnt_drop, cnt_frame_err;
    wire [31:0]        cnt_pkt;

    integer errors = 0, n_tests = 0;

    always #5 clk = ~clk;

    uart_rx #(.CLK_FREQ(CLK_FREQ), .BAUD(BAUD)) u_rx (
        .clk(clk), .rst_n(rst_n), .rx(rx),
        .byte_out(byte_out), .byte_valid(byte_valid), .frame_err(frame_err));

    pkt_parser #(.N_CH(8), .CLK_FREQ(CLK_FREQ), .BAUD(BAUD), .TOUT_BYTE(2)) u_pp (
        .clk(clk), .rst_n(rst_n),
        .byte_in(byte_out), .byte_valid(byte_valid), .frame_err(frame_err),
        .sample_valid(sample_valid), .ch_data(ch_data), .sample_seq(sample_seq),
        .pkt_type(pkt_type),
        .cnt_footer_err(cnt_footer_err), .cnt_sync_err(cnt_sync_err),
        .cnt_drop(cnt_drop), .cnt_frame_err(cnt_frame_err), .cnt_pkt(cnt_pkt));

    // ── 样本捕获：**只由 always 驱动**（避免与 initial 形成多驱动）──
    reg signed [23:0] cap_ch [0:7];
    reg [7:0]         cap_seq;
    reg [3:0]         cap_type;
    integer           cap_count     = 0;      // 唯一判据
    integer           cap_last_seq  = -1;
    integer           cap_last_type = -1;

    always @(posedge clk) begin
        if (sample_valid) begin
            cap_ch[0] <= ch_data[0];  cap_ch[1] <= ch_data[1];
            cap_ch[2] <= ch_data[2];  cap_ch[3] <= ch_data[3];
            cap_ch[4] <= ch_data[4];  cap_ch[5] <= ch_data[5];
            cap_ch[6] <= ch_data[6];  cap_ch[7] <= ch_data[7];
            cap_seq       <= sample_seq;
            cap_type      <= pkt_type;
            cap_last_seq  = sample_seq;
            cap_last_type = pkt_type;
            cap_count     = cap_count + 1;
        end
    end

    // ── 串口发送 ─────────────────────────────────────────────────
    task send_byte(input [7:0] b);
        integer i;
        begin
            rx = 1'b0; #(BIT_NS);
            for (i = 0; i < 8; i = i + 1) begin rx = b[i]; #(BIT_NS); end
            rx = 1'b1; #(BIT_NS);
        end
    endtask

    // ── 组包：8 通道大端值 = base 各字节 + 通道号 ──
    reg [7:0] pkt [0:32];
    task build_packet(input [7:0] seq, input [23:0] base, input [7:0] footer);
        integer c;
        begin
            pkt[0] = 8'h41;
            pkt[1] = seq;
            for (c = 0; c < 8; c = c + 1) begin
                pkt[2 + 3*c] = base[23:16] + c[7:0];
                pkt[3 + 3*c] = base[15:8]  + c[7:0];
                pkt[4 + 3*c] = base[7:0]   + c[7:0];
            end
            for (c = 26; c < 32; c = c + 1) pkt[c] = 8'hEE;
            pkt[32] = footer;
        end
    endtask

    task send_packet();
        integer i;
        begin
            for (i = 0; i < 33; i = i + 1) send_byte(pkt[i]);
        end
    endtask

    task check(input cond, input [255:0] tag);
        begin
            n_tests = n_tests + 1;
            if (cond) $display("  [ OK ] %0s", tag);
            else begin $display("  [FAIL] %0s", tag); errors = errors + 1; end
        end
    endtask

    integer c0, pk0, fe0, se0, dr0;

    initial begin
        $dumpfile("tb_pkt_parser.vcd");
        $dumpvars(0, tb_pkt_parser);

        $display("=== tb_pkt_parser ===");
        $display("位周期 %.0f ns   字节周期 %.0f ns   超时阈值 %.0f ns (2 字节)",
                 BIT_NS, BYTE_NS, BYTE_NS * 2);

        rst_n = 0; #200; rst_n = 1; #2000;

        // ══ T1 合法包 ══
        $display("\n[T1] 合法 33 字节包");
        c0 = cap_count;
        build_packet(8'd7, 24'h123456, 8'hC0);
        send_packet();
        #(BYTE_NS);
        check(cap_count == c0 + 1, "收到 1 个样本");
        check(cap_last_seq === 7, "sample_seq = 7");
        check(cap_last_type === 0, "pkt_type = 0 (ACCEL)");
        check(cap_ch[0] === 24'sh123456, "ch0 = 0x123456");
        check(cap_ch[7] === 24'sh193B5D, "ch7 = 0x193B5D");

        // 负数
        c0 = cap_count;
        pkt[0]=8'h41; pkt[1]=8'd8;
        pkt[2]=8'hFF; pkt[3]=8'hFF; pkt[4]=8'hF0;      // -16
        for (integer k = 5;  k <= 25; k = k + 1) pkt[k] = 8'h00;
        for (integer k = 26; k <= 31; k = k + 1) pkt[k] = 8'hEE;
        pkt[32] = 8'hC0;
        send_packet();
        #(BYTE_NS);
        check(cap_count == c0 + 1, "负数样本收到");
        check(cap_ch[0] === -24'sd16, "ch0 = -16（符号正确）");

        // ══ T2a 纯垃圾前缀 ══
        $display("\n[T2a] 纯垃圾前缀（不含 0x41）→ 立即同步");
        c0 = cap_count;
        send_byte(8'h99); send_byte(8'h88); send_byte(8'h77);
        build_packet(8'd9, 24'hABCDEF, 8'hC0);
        send_packet();
        #(BYTE_NS);
        check(cap_count == c0 + 1, "垃圾后收到样本");
        check(cap_last_seq === 9, "同步到 seq=9");
        check(cap_ch[0] === 24'shABCDEF, "ch0 = 0xABCDEF");

        // ══ T2b 假 BOP ══
        $display("\n[T2b] 假 BOP → 包尾校验拒绝，下一包恢复");
        c0 = cap_count;
        send_byte(8'h41); send_byte(8'h00);
        build_packet(8'd20, 24'hAAAAAA, 8'hC0); send_packet();   // 被吞
        build_packet(8'd21, 24'hBBBBBB, 8'hC0); send_packet();   // 应正常
        #(BYTE_NS);
        check(cap_count == c0 + 1, "只产出 1 个样本（前一包被吞）");
        check(cap_last_seq === 21, "重新同步到 seq=21");
        check(cap_ch[0] === 24'shBBBBBB, "ch0 = 0xBBBBBB");

        // ══ T3 包尾非法 ══
        $display("\n[T3] 包尾非法");
        c0 = cap_count; fe0 = cnt_footer_err;
        build_packet(8'd10, 24'h111111, 8'hA5);
        send_packet();
        #(BYTE_NS);
        check(cap_count == c0, "非法包尾不输出样本");
        check(cnt_footer_err === fe0 + 8'd1, "cnt_footer_err +1");

        // ══ T4 包内间隔超时 ══
        $display("\n[T4] 包内字节间隔超时");
        c0 = cap_count; se0 = cnt_sync_err;
        build_packet(8'd11, 24'h222222, 8'hC0);
        send_byte(pkt[0]); send_byte(pkt[1]);
        #(BYTE_NS * 2.5);              // ★ 必须 > 2 个字节时间才触发阈值
        for (integer k = 2; k < 33; k = k + 1) send_byte(pkt[k]);
        #(BYTE_NS);
        $display("       [诊断] sync_err %0d -> %0d", se0, cnt_sync_err);
        check(cnt_sync_err === se0 + 8'd1, "cnt_sync_err +1（判定断流）");
        check(cap_count == c0, "断流后本包不输出");

        c0 = cap_count;
        build_packet(8'd12, 24'h333333, 8'hC0);
        send_packet();
        #(BYTE_NS);
        $display("       [诊断] 恢复包: cap_count %0d -> %0d, last_seq=%0d, cnt_pkt=%0d", c0, cap_count, cap_last_seq, cnt_pkt);
        check(cap_count == c0 + 1 && cap_last_seq === 12, "断流后可正常恢复");

        // ══ T5 序号跳变 ══
        $display("\n[T5] 序号跳变（丢包检测）");
        c0 = cap_count; dr0 = cnt_drop;
        build_packet(8'd16, 24'h444444, 8'hC0);     // 12 -> 16，丢 3 个
        send_packet();
        #(BYTE_NS);
        $display("       [诊断] drop %0d -> %0d", dr0, cnt_drop);
        $display("       [诊断] T5: cap_count %0d -> %0d, last_seq=%0d", c0, cap_count, cap_last_seq);
        check(cap_count == c0 + 1, "样本收到");
        check(cnt_drop === dr0 + 8'd3, "cnt_drop +3");

        // ══ T6 连续包计数 ══
        $display("\n[T6] 连续 3 包");
        c0 = cap_count; pk0 = cnt_pkt;
        build_packet(8'd17, 24'h555555, 8'hC0); send_packet();
        build_packet(8'd18, 24'h555555, 8'hC1); send_packet();
        build_packet(8'd19, 24'h555555, 8'hC2); send_packet();
        #(BYTE_NS);
        check(cap_count == c0 + 3, "收到 3 个样本");
        check(cnt_pkt === pk0 + 32'd3, "cnt_pkt +3");
        check(cap_last_type === 4'h2, "pkt_type 跟随包尾低 4 位");

        // ══ 汇总 ══
        $display("\n========================================");
        if (errors == 0) $display(" 全部通过（%0d 项）", n_tests);
        else             $display(" 失败 %0d / %0d 项", errors, n_tests);
        $display("  统计: cnt_pkt=%0d footer_err=%0d sync_err=%0d drop=%0d",
                 cnt_pkt, cnt_footer_err, cnt_sync_err, cnt_drop);
        $display("========================================");
        $finish;
    end

    initial begin
        #(BYTE_NS * 700);
        $display("!! 仿真超时");
        $finish;
    end

endmodule

`default_nettype wire
