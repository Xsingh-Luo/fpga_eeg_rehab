//=============================================================================
// tb_datapath.v —— 数据通路逐级对拍
//=============================================================================
// 链路：csp_project → win_var → lognorm → svm_core
//
// 每一级都 dump 出来，由 verify_datapath.py 与 Python 逐位参考模型比对。
// ★ 逐级 dump 的意义：最终标签只有 1 bit，中间任何一级错了它都可能"碰巧对"；
//   逐级对拍才能在出错时立刻定位。
//
// 激励与规模由编译期宏给定（见 verify_datapath.py）。
// ★ 用有界 for + $fscanf 读十进制，不用 while(!$feof) —— 后者在 iverilog 里
//   会因 feof 语义踩死循环（本项目踩过一次，见规划/10 §2 问题 6 的姊妹问题）。
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module tb_datapath;

`ifndef N_SAMP
    `define N_SAMP   200
`endif
`ifndef N_CH
    `define N_CH     2
`endif
`ifndef N_COMP
    `define N_COMP   2
`endif
`ifndef WIN_LEN
    `define WIN_LEN  64
`endif
`ifndef FEAT_DIM
    `define FEAT_DIM 2
`endif

    localparam integer N_SAMP   = `N_SAMP;
    localparam integer N_CH     = `N_CH;
    localparam integer N_COMP   = `N_COMP;
    localparam integer WIN_LEN  = `WIN_LEN;
    localparam integer FEAT_DIM = `FEAT_DIM;

    reg clk = 0, rst_n = 0, en = 0, en_win = 0;
    always #5 clk = ~clk;
    // ★ 流水线使能必须逐级延迟：
    //   csp_project 在 en 沿寄存 z，win_var 若用同一个 en，
    //   在同一沿读到的是**上一个** z —— 整个窗和会错位一个采样。
    //   （现象：L2 的 Sz 最大差 161，L3/L4 跟着全错）
    //   顶层 eeg_csp_svm_top 也用同样的结构。

    // ── 当前样本 / 权重（直接接到 DUT）──
    reg  signed [23:0] x_cur [0:N_CH-1];
    reg  signed [17:0] w_csp [0:N_CH*N_COMP-1];
    reg  signed [15:0] w_svm [0:FEAT_DIM-1];
    reg  signed [31:0] b_svm;

    // ── 激励存储 ──
    reg signed [23:0] x_all [0:N_SAMP*N_CH-1];

    // ── L1 CSP ──
    wire signed [23:0] z [0:N_COMP-1];
    csp_project #(.N_CH(N_CH), .N_COMP(N_COMP)) u_csp (
        .clk(clk), .rst_n(rst_n), .en(en), .x(x_cur), .w(w_csp), .z(z));

    // ── L2 win_var ──
    wire signed [47:0] sz [0:N_COMP-1], sz2 [0:N_COMP-1];
    wire win_full;
    win_var #(.N_COMP(N_COMP), .WIN_LEN(WIN_LEN)) u_win (
        .clk(clk), .rst_n(rst_n), .en(en_win), .clear(1'b0), .z(z),
        .sz(sz), .sz2(sz2), .win_full(win_full));

    // ── L3 lognorm ──
    reg  ln_start = 0;
    wire ln_done;
    wire signed [15:0] f [0:N_COMP-1];
    lognorm #(.N_COMP(N_COMP), .WIN_LEN(WIN_LEN)) u_ln (
        .clk(clk), .rst_n(rst_n), .start(ln_start), .sz(sz), .sz2(sz2),
        .done(ln_done), .f(f));

    // ── L4 svm ──
    wire signed [31:0] d;
    wire               lbl;
    svm_core #(.FEAT_DIM(FEAT_DIM)) u_svm (
        .clk(clk), .rst_n(rst_n), .en(ln_done),
        .f(f[0:FEAT_DIM-1]), .w(w_svm), .b(b_svm), .d(d), .label(lbl));

    // ── dump ──
    integer fd_z, fd_sz, fd_sz2, fd_f, fd_d, fd_lbl, fd_x, fd_w, fd_ws, fd_m;
    integer i, j, n, code, tmp;

    initial begin
        fd_x  = $fopen("dp_x.txt",    "r");
        fd_w  = $fopen("dp_w.txt",    "r");
        fd_ws = $fopen("dp_wsvm.txt", "r");
        fd_m  = $fopen("dp_meta.txt", "r");
        if (fd_x == 0 || fd_w == 0 || fd_ws == 0 || fd_m == 0) begin
            $display("!! 缺激励文件，请先运行 verify_datapath.py");
            $finish;
        end
        code = $fscanf(fd_m, "%d %d %d %d %d %d\n", tmp, tmp, tmp, tmp, tmp, b_svm);
        for (i = 0; i < N_SAMP*N_CH; i = i + 1)
            code = $fscanf(fd_x, "%d\n", x_all[i]);
        for (i = 0; i < N_CH*N_COMP; i = i + 1)
            code = $fscanf(fd_w, "%d\n", w_csp[i]);
        for (i = 0; i < FEAT_DIM; i = i + 1)
            code = $fscanf(fd_ws, "%d\n", w_svm[i]);
        $fclose(fd_x); $fclose(fd_w); $fclose(fd_ws); $fclose(fd_m);

        fd_z   = $fopen("dp_z.txt",   "w");
        fd_sz  = $fopen("dp_sz.txt",  "w");
        fd_sz2 = $fopen("dp_sz2.txt", "w");
        fd_f   = $fopen("dp_f.txt",   "w");
        fd_d   = $fopen("dp_d.txt",   "w");
        fd_lbl = $fopen("dp_lbl.txt", "w");

        rst_n = 0; #200; rst_n = 1; #100;

        // ── 逐样本推进 ──
        for (n = 0; n < N_SAMP; n = n + 1) begin
            for (i = 0; i < N_CH; i = i + 1)
                x_cur[i] = x_all[n*N_CH + i];

            // ① csp 在 en 沿更新 z
            en = 1; #10; en = 0;
            // ② 等一拍，让 win_var 消费这个 z
            #10;
            en_win = 1; #10; en_win = 0;
            // ③ 等一拍，让 sz/sz2 稳定
            #20;

            for (j = 0; j < N_COMP; j = j + 1) $fdisplay(fd_z, "%0d", z[j]);
            for (j = 0; j < N_COMP; j = j + 1) begin
                $fdisplay(fd_sz,  "%0d", sz[j]);
                $fdisplay(fd_sz2, "%0d", sz2[j]);
            end
        end

        // ── 窗口已满 → 跑一次 lognorm + svm ──
        #50;
        ln_start = 1; #10; ln_start = 0;
        for (i = 0; i < 200 && !ln_done; i = i + 1) #10;
        #40;
        for (j = 0; j < N_COMP; j = j + 1) $fdisplay(fd_f, "%0d", f[j]);
        $fdisplay(fd_d,   "%0d", d);
        $fdisplay(fd_lbl, "%0d", lbl);

        $display("[dp] 完成: %0d 样本, win_full=%b, f0=%0d f1=%0d, d=%0d lbl=%0d",
                 N_SAMP, win_full, f[0], f[1], d, lbl);
#100; $finish;
    end

endmodule

`default_nettype wire
