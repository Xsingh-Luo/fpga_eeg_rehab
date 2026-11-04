//=============================================================================
// tb_cosim.v —— 联合仿真：真实 Cyton 字节流 → uart_rx → pkt_parser → biquad
//=============================================================================
// 激励来自 tools/sample_stream.bin（744 包，cyton_sim.py 生成，
// 版本管理里列为「不可再生的验证资产」）。RTL 与 PC 端 cyton_parse.py
// 吃同一份激励，证明两边对协议的理解完全一致。
//
// ★ 关于时间尺度：
//   真实 DIV = 100MHz/115200 = 868，744 包要跑 2.13 亿个周期，iverilog 得跑一小时以上。
//   所以这里用**等比缩小**的参数：CLK_FREQ=1000, BAUD=100 → DIV=10。
//   逻辑完全一致（状态机、索引、校验、超时判据都不变），只是绝对时间缩短。
//   真实 DIV 下的**位宽验证**由 tb_pkt_parser.v 覆盖（它用 DIV=868，23 项全过）。
//
// 输出：
//   dump_raw.txt —— 每包 3 个通道值（十进制，供 Python 比对）
//   dump_bq.txt  —— 通道 0 经过带 0（2 节 biquad）后的输出
//
// 运行：由 cosim_cyton.py 自动调用
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module tb_cosim;

    // ★ 等比缩小的时钟参数（见文件头说明）
    //   时钟固定 100 MHz（周期 10 ns），只把「每位的时钟数」DIV 从 868 缩到 10。
    //   ⚠️ 第一版写成 BIT_NS = 1e9*DIV/CLK_FREQ，在 CLK_FREQ=1000 时算出 1e7 ns = 10 ms，
    //      而时钟周期只有 10 ns —— 每位要 100 万拍，仿真是跑不完的。
    //      正确算法是「位周期 = DIV × 时钟周期」。
    localparam integer CLK_FREQ = 1000;
    localparam integer BAUD     = 100;
    localparam integer DIV      = CLK_FREQ / BAUD;      // 10 个时钟/位
    localparam real    CLK_NS   = 10.0;                 // 时钟周期 10 ns
    localparam real    BIT_NS   = DIV * CLK_NS;         // = 100 ns/位

    reg clk = 0, rst_n = 0, rx = 1;
    wire [7:0] byte_out;
    wire       byte_valid, frame_err;

    wire               sample_valid;
    wire signed [23:0] ch_data [0:7];
    wire [7:0]         sample_seq;
    wire [3:0]         pkt_type;
    wire [7:0]         cfe, cse, cdr, cfr;
    wire [31:0]        cpk;

    // 8 位十六进制字节流 —— 大小由编译期宏给定（$readmemh 比 $fscanf 快得多，
    // 且不会因为 feof 语义踩死循环）
`ifndef N_BYTE
    `define N_BYTE 24552
`endif
`ifndef N_PKT
    `define N_PKT 744
`endif
    localparam integer N_BYTE_P = `N_BYTE;
    localparam integer N_PKT_P  = `N_PKT;

    reg [7:0] mem [0:N_BYTE_P-1];

    integer   n_byte, n_pkt, i, fd_out_raw, fd_out_bq;

    always #5 clk = ~clk;

    uart_rx #(.CLK_FREQ(CLK_FREQ), .BAUD(BAUD)) u_rx (
        .clk(clk), .rst_n(rst_n), .rx(rx),
        .byte_out(byte_out), .byte_valid(byte_valid), .frame_err(frame_err));

    pkt_parser #(.N_CH(8), .CLK_FREQ(CLK_FREQ), .BAUD(BAUD), .TOUT_BYTE(2)) u_pp (
        .clk(clk), .rst_n(rst_n),
        .byte_in(byte_out), .byte_valid(byte_valid), .frame_err(frame_err),
        .sample_valid(sample_valid), .ch_data(ch_data), .sample_seq(sample_seq),
        .pkt_type(pkt_type),
        .cnt_footer_err(cfe), .cnt_sync_err(cse), .cnt_drop(cdr),
        .cnt_frame_err(cfr), .cnt_pkt(cpk));

    // ── biquad：只做通道 0 的带 0（2 节），系数由 cosim_cyton.py 通过
    //    stim_cyton.hex 后面的行传入不现实，这里直接内联（与 fbcsp_filters.mat 一致）
    //    带0 节0 / 节1 的 Q3.14 值 ──
    reg signed [17:0] c00_b0, c00_b1, c00_b2, c00_a1, c00_a2;
    reg signed [17:0] c01_b0, c01_b1, c01_b2, c01_a1, c01_a2;

    reg bq_en = 0;
    wire signed [23:0] bq_out;
    wire signed [23:0] bq_in = ch_data[0];

    biquad_cascade #(.DW(24), .CW(18), .Q(14)) u_bq (
        .clk(clk), .rst_n(rst_n), .en(bq_en), .x(bq_in),
        .b0_0(c00_b0), .b1_0(c00_b1), .b2_0(c00_b2), .a1_0(c00_a1), .a2_0(c00_a2),
        .b0_1(c01_b0), .b1_1(c01_b1), .b2_1(c01_b2), .a1_1(c01_a1), .a2_1(c01_a2),
        .y(bq_out));

    // sample_valid 拉高 1 拍 → 产生一个 en 脉冲；级联内部再延 1 拍
    reg sv_d1 = 0, sv_d2 = 0, sv_d3 = 0;
    always @(posedge clk) begin
        sv_d1 <= sample_valid;
        sv_d2 <= sv_d1;
        sv_d3 <= sv_d2;
    end
    always @(posedge clk) bq_en <= sample_valid;

    integer n_out = 0;
    always @(posedge clk) begin
        if (sample_valid) begin
            $fdisplay(fd_out_raw, "%0d %0d %0d", ch_data[0], ch_data[1], ch_data[2]);
        end
        // 级联 3 拍后输出有效：bq_en 1 拍 + 级联内部 en_d 1 拍 + 输出寄存 1 拍。
        // ★ 第一版抓的 sv_d2，整体差一拍 —— 现象是 biquad 输出 732/744 不一致、
        //   最大差 92。加一级到 sv_d3 后应逐位一致。
        if (sv_d3) begin
            $fdisplay(fd_out_bq, "%0d", bq_out);
            n_out = n_out + 1;
        end
    end

    integer code, cval, head, fd_hex;
    initial begin
        // ── 系数（与 python gen_biquad_tv.py 打印的一致）──
        c00_b0 = 18'sd145;    c00_b1 = 18'sd289;    c00_b2 = 18'sd145;
        c00_a1 = -18'sd28103; c00_a2 = 18'sd13728;
        c01_b0 = 18'sd16384;  c01_b1 = -18'sd32768; c01_b2 = 18'sd16384;
        c01_a1 = -18'sd30346; c01_a2 = 18'sd14714;

        $display("[cosim] 读激励 stim_cyton.hex（%0d 字节）", N_BYTE_P);
        $readmemh("stim_cyton.hex", mem);
        n_byte = N_BYTE_P;
        n_pkt  = N_PKT_P;
        $display("[cosim] 包数 %0d，字节数 %0d", n_pkt, n_byte);

        fd_out_raw = $fopen("dump_raw.txt", "w");
        fd_out_bq  = $fopen("dump_bq.txt",  "w");

        // ── 复位 ──
        rst_n = 0; #200; rst_n = 1; #200;

        // ── 按真实串口时序逐字节发出 ──
        for (i = 0; i < n_byte; i = i + 1) send_byte(mem[i]);

        #(BIT_NS * 40);
        $fclose(fd_out_raw);
        $fclose(fd_out_bq);

        $display("[cosim] 完成: cnt_pkt=%0d footer_err=%0d sync_err=%0d drop=%0d frame_err=%0d",
                 cpk, cfe, cse, cdr, cfr);
        $display("[cosim] 原始样本 %0d 个，biquad 输出 %0d 个", cpk, n_out);
        if (cpk != n_pkt)
            $display("[cosim] !! 期望 %0d 包，实得 %0d", n_pkt, cpk);
        $finish;
    end


    task send_byte(input [7:0] b);
        integer k;
        begin
            rx = 1'b0; #(BIT_NS);
            for (k = 0; k < 8; k = k + 1) begin rx = b[k]; #(BIT_NS); end
            rx = 1'b1; #(BIT_NS);
        end
    endtask

endmodule

`default_nettype wire
