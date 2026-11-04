//=============================================================================
// tb_biquad_bank.v —— biquad 定点逐位对拍
//=============================================================================
// 读入 gen_biquad_tv.py 生成的测试向量，把 RTL 输出与 Python 逐位模型的
// 期望输出**逐样本比对**，任何一个 LSB 不一致都算失败。
//
// 这是规划/05 §5「四级对拍」里第 2 级（定点 RTL vs 定点参考）的实现。
//
// 运行：
//   python gen_biquad_tv.py
//   iverilog -g2012 -o tb_bq.vvp tb_biquad_bank.v ../rtl/biquad_bank.v && vvp tb_bq.vvp
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module tb_biquad_bank;

    localparam integer DW = 24;
    localparam integer CW = 18;
    localparam integer Q  = 14;
    localparam integer MAXS = 2048;

    reg clk = 0, rst_n = 0, en = 0;
    reg  signed [DW-1:0] x;
    reg  signed [CW-1:0] coef [0:9];
    wire signed [DW-1:0] y;

    integer errors = 0, n_samp = 0, i, fd, code, n_coef, b0, b1, b2;
    integer in_mem [0:MAXS-1];
    integer ex_mem [0:MAXS-1];
    integer got, exp_v;

    always #5 clk = ~clk;

    biquad_cascade #(.DW(DW), .CW(CW), .Q(Q)) dut (
        .clk(clk), .rst_n(rst_n), .en(en), .x(x),
        .b0_0(coef[0]), .b1_0(coef[1]), .b2_0(coef[2]), .a1_0(coef[3]), .a2_0(coef[4]),
        .b0_1(coef[5]), .b1_1(coef[6]), .b2_1(coef[7]), .a1_1(coef[8]), .a2_1(coef[9]),
        .y(y)
    );

    // 读一个 3 字节大端的值，拼成有符号整数（bits 位）
    task read_signed(input integer bits, output integer v);
        integer hi, mid, lo;
        begin
            code = $fscanf(fd, "%h\n", hi);
            code = $fscanf(fd, "%h\n", mid);
            code = $fscanf(fd, "%h\n", lo);
            v = (hi << 16) | (mid << 8) | lo;
            if (bits == 18) v = v & 18'h3FFFF;              // 18 位掩码
            // 符号扩展
            if (v >= (1 << (bits-1))) v = v - (1 << bits);
        end
    endtask

    integer tv, nline;
    initial begin
        $dumpfile("tb_biquad_bank.vcd");
        $dumpvars(0, tb_biquad_bank);

        $display("=== tb_biquad_bank 定点逐位对拍 ===");

        fd = $fopen("tv_biquad.hex", "r");
        if (fd == 0) begin
            $display("!! 找不到 tv_biquad.hex，请先运行 gen_biquad_tv.py");
            $finish;
        end

        // 系数个数
        code = $fscanf(fd, "%h\n", n_coef);
        if (n_coef != 10) begin
            $display("!! 系数个数 %0d != 10", n_coef);
            $finish;
        end
        for (i = 0; i < n_coef; i = i + 1) begin
            read_signed(18, tv);
            coef[i] = tv[CW-1:0];
        end
        $display("系数: b0_0=%0d b1_0=%0d b2_0=%0d a1_0=%0d a2_0=%0d",
                 coef[0], coef[1], coef[2], coef[3], coef[4]);
        $display("      b0_1=%0d b1_1=%0d b2_1=%0d a1_1=%0d a2_1=%0d",
                 coef[5], coef[6], coef[7], coef[8], coef[9]);

        // 样本数（2 字节小端）
        code = $fscanf(fd, "%h\n", b0);
        code = $fscanf(fd, "%h\n", b1);
        n_samp = b0 | (b1 << 8);
        $display("样本数: %0d", n_samp);
        if (n_samp > MAXS) begin $display("!! 超出 MAXS"); $finish; end

        for (i = 0; i < n_samp; i = i + 1) read_signed(24, in_mem[i]);
        for (i = 0; i < n_samp; i = i + 1) read_signed(24, ex_mem[i]);
        $fclose(fd);

        // ── 复位 ──
        rst_n = 0; #200; rst_n = 1; #20;

        // ── 逐样本跑，比对 ──
        // ★ 级联总延迟 = 2 个 en 节拍（节0 一拍 + 节1 一拍）。
        //   en 只拉高 1 个时钟，再等 2 个时钟让两节都走完，然后读 y。
        for (i = 0; i < n_samp; i = i + 1) begin
            x  = in_mem[i][DW-1:0];
            en = 1; #10; en = 0; #20;
            got   = y;
            exp_v = ex_mem[i];
            if (got !== exp_v) begin
                if (errors < 10)
                    $display("  [FAIL] n=%0d  x=%0d  期望 %0d  实得 %0d  (差 %0d)",
                             i, in_mem[i], exp_v, got, got - exp_v);
                errors = errors + 1;
            end
        end

        $display("\n========================================");
        if (errors == 0) $display(" 全部 %0d 个样本逐位一致 ✓", n_samp);
        else             $display(" 不一致 %0d / %0d 个样本", errors, n_samp);
        $display("========================================");
        $finish;
    end

endmodule

`default_nettype wire
