//=============================================================================
// svm_core.v —— M8：线性 SVM 判决
//=============================================================================
// 对应设计文档：规划/02 算法实现规格书.md §4.7
//
// 公式：
//     d = w · f + b        （折叠后，见下）
//     标签 = (d > 0)
//
// ★ 线性核为什么能折叠
//     d(f) = Σ_i α_i y_i (sv_i · f) + b
//          = ( Σ_i α_i y_i sv_i ) · f + b
//          = w · f + b
//   实测：MATLAB 里折叠前后决策值最大差 1.8e-13，预测标签完全一致。
//   B01 有 261 个支持向量，折叠后 PL 只需存 **4 个权重 + 1 个偏置**。
//
// ★ 为什么不上 RBF
//   实测线性核与 RBF/NBPW 在本任务上打平（77.40% vs 77.88%），
//   而 RBF 要在 PL 上做 K 次 exp（K=261），代价高两个数量级。
//   见规划/07 §3 的裁决表。
//
// 定点格式：
//     f     : Q4.11 / 16 bit
//     w     : Q4.11 / 16 bit（与特征同域）
//     b     : Q8.23 / 32 bit
//     乘积  : Q8.22，累加后用 32 位
//     d     : Q8.23 / 32 bit
//
// 时序：n_comp 个乘法器 + 加法树，1 拍出结果。FEAT_DIM 只有 4，无压力。
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module svm_core #(
    parameter integer FEAT_DIM = 4,
    parameter integer FW       = 16,      // 特征/权重位宽
    parameter integer QF       = 11,      // 特征小数位 Q4.11
    parameter integer QB       = 23,      // 偏置小数位 Q8.23
    parameter integer DW       = 32       // 决策值位宽
) (
    input  wire                       clk,
    input  wire                       rst_n,
    input  wire                       en,                       // 决策节拍
    input  wire signed [FW-1:0]       f [0:FEAT_DIM-1],         // Q4.11
    input  wire signed [FW-1:0]       w [0:FEAT_DIM-1],         // Q4.11
    input  wire signed [DW-1:0]       b,                        // Q8.23
    output reg  signed [DW-1:0]       d,                        // Q8.23
    output reg                        label                     // 1 = 正类
);

    // Σ w_i · f_i ：w、f 都是 Q4.11 → 乘积 Q8.22
    // 偏置是 Q8.23，所以累加结果要左移 1 位才能与之相加。
    //
    // ★★ 累加器只需 40 位，不要用 64 位
    //   量级上界：FEAT_DIM × |w|max × |f|max = 4 × 32767 × 32767 ≈ 2^32
    //   → <<1 后 2^33，再加 32 位偏置 → 2^34，40 位有充足余量。
    //   第一版用 64 位，综合后最差路径就是这里的 `sum = acc_q23 + b`：
    //     u_svm/acc_q231/CLK → u_svm/d_reg[17]/D
    //     Data Path 11.105 ns，Logic Levels 17（**CARRY4 = 11**），WNS = −1.254 ns
    //   64 位加法器要 16 级 CARRY4；40 位只要 10 级，配合下面的饱和逻辑即可收敛。
    localparam integer ACCW = 40;

    integer i;
    reg signed [ACCW-1:0] acc;
    always @(*) begin
        acc = {ACCW{1'b0}};
        for (i = 0; i < FEAT_DIM; i = i + 1)
            acc = acc + w[i] * f[i];
    end

    // ══════════════════════════════════════════════════════════
    //  ★★ 两级流水（WNS = −1.556 ns 时最差路径就在本模块）
    //
    //  原路径：u_svm/acc_q231 → u_svm/d_reg[16]
    //          Data Path 11.419 ns（logic 9.366 + route 2.053）
    //          Logic Levels 14（CARRY4=9 DSP48E1=3）
    //  一拍里同时做了「4 项乘加树 → 40 位左移 → 加偏置 → 饱和 → 打拍」。
    //
    //  切开：
    //    级 1（en）  ：4 项乘加树 → acc_r
    //    级 2（en_d）：acc_r → <<1 → 加偏置 → 饱和 → d/label
    //  代价 1 拍延迟。决策率 250 Hz、采样间隔 400,000 拍，**完全无所谓**。
    // ══════════════════════════════════════════════════════════
    reg en_d;
    always @(posedge clk) en_d <= rst_n & en;

    reg signed [ACCW-1:0] acc_r;
    always @(posedge clk) begin
        if (!rst_n)  acc_r <= {ACCW{1'b0}};
        else if (en) acc_r <= acc;
    end

    // Q8.22 → Q8.23（左移 1 位）；用 ACCW 位中间量防止左移丢失符号
    wire signed [ACCW-1:0] acc_q23 = acc_r <<< 1;
    wire signed [ACCW-1:0] sum     = acc_q23 + {{(ACCW-DW){b[DW-1]}}, b};

    // ★ 饱和检测只看**高位**，不做 40 位比较
    //   原先写 (sum > 32 位上限) 这种比较，综合后是 11 级 CARRY4 的减法器。
    //   改成对 sum[ACCW-2 : DW-1] 做归约：
    //     正溢出：sum 为正 且 除符号位外还有高位为 1
    //     负溢出：sum 为负 且 除符号位外并非全 1
    wire ovf_pos = ~sum[ACCW-1] &  (|sum[ACCW-2 : DW-1]);
    wire ovf_neg =  sum[ACCW-1] & ~(&sum[ACCW-2 : DW-1]);
    wire signed [DW-1:0] d_sat =
        ovf_pos ? {1'b0, {(DW-1){1'b1}}} :
        ovf_neg ? {1'b1, {(DW-1){1'b0}}} :
                  sum[DW-1:0];

    always @(posedge clk) begin
        if (!rst_n) begin
            d     <= {DW{1'b0}};
            label <= 1'b0;
        end else if (en_d) begin
            d     <= d_sat;
            label <= ~d_sat[DW-1];      // 符号位为 0 → d > 0 → 正类
        end
    end

endmodule

`default_nettype wire
