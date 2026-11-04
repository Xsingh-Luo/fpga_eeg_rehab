//=============================================================================
// csp_project.v —— M5：CSP 空间投影
//=============================================================================
// 对应设计文档：规划/02 算法实现规格书.md §4.4
//
// 公式：
//     z_j = Σ_c  W_eff[c][j] · x[c]        j = 0 .. n_comp-1
//
// 其中 W_eff 是**已含欧氏对齐折叠**的矩阵：
//     W_eff = M^T · W~,   M = R_bar^(-1/2)
//
// ★ 为什么 EA 能折进来（本项目最值钱的一条）：
//     z = W~^T · (M·x) = (M^T·W~)^T · x = W_eff^T · x
//   白化矩阵离线乘进 CSP 投影后，推理链路完全不感知 EA 的存在 —— **PL 零改动**。
//   代价：W_eff 的动态范围比 W~ 大（实测 |W_eff|max = 3.81 vs 未折叠 < 2.0），
//   所以格式必须从 Q2.14/16bit 升到 **Q3.14/18bit**，否则溢出。
//
// 定点格式：
//     x     : Q8.15  / 24 bit
//     W_eff : Q3.14  / 18 bit
//     乘积  : Q11.29 / 42 bit → 累加 48 bit
//     z     : Q10.13 / 24 bit（>>16 后饱和）
//
// 为什么全并行：N_CH × n_comp = 8 × 2 = 16 个乘法器。
//   时间复用只要 16 个周期，但那样 CSP 就得缓存 3 个带的样本、增加握手逻辑；
//   而 8 通道 × 3 带 = 48 个乘法器也就 48 个 DSP（占 7020 的 22%），
//   在"每 4 ms 只有一个采样点"的节拍下用满并行换取**最简控制流**是划算的。
//   （若资源紧张，可按规划/07 §4.3 改为时分复用，代价是控制状态机复杂度。）
//
// 时序：关键路径 = 18×24 乘法 + 48 位加法树。建议累加树打 1~2 级寄存器，
//   综合时若报 setup 违例，优先在加法树中间插寄存器（见调试报告 §3）。
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module csp_project #(
    parameter integer N_CH   = 8,
    parameter integer N_COMP = 2,
    parameter integer DW     = 24,     // x 位宽
    parameter integer CW     = 18,     // W 位宽
    parameter integer QW     = 14,     // W 小数位
    parameter integer QX     = 15,     // x 小数位
    parameter integer QZ     = 13,     // z 小数位
    parameter integer AW     = 48
) (
    input  wire                     clk,
    input  wire                     rst_n,
    input  wire                     en,                    // 250 Hz 节拍
    input  wire signed [DW-1:0]     x  [0:N_CH-1],         // Q8.15
    // W_eff：行优先 N_CH × N_COMP，已含 EA 折叠
    input  wire signed [CW-1:0]     w  [0:N_CH*N_COMP-1],
    output reg  signed [DW-1:0]     z  [0:N_COMP-1]        // Q10.13
);

    // 移位量：Q11.29 → Q10.13 需要右移 16
    localparam integer SHIFT = QW + QX - QZ;    // 14 + 15 - 13 = 16

    genvar j;
    generate
        for (j = 0; j < N_COMP; j = j + 1) begin : g_comp
            // ── 组合累加树 ──
            integer c;
            reg signed [AW-1:0] acc;
            always @(*) begin
                acc = {AW{1'b0}};
                for (c = 0; c < N_CH; c = c + 1)
                    acc = acc + w[c*N_COMP + j] * x[c];
            end

            wire signed [AW-1:0] z_ext = acc >>> SHIFT;

            // 饱和到 24 位
            // ★ 高位归约代替 48 位比较（同 dc_block / biquad / svm）
            wire ovf_pos = ~z_ext[AW-1] &  (|z_ext[AW-2 : DW-1]);
            wire ovf_neg =  z_ext[AW-1] & ~(&z_ext[AW-2 : DW-1]);
            wire signed [DW-1:0] z_sat =
                ovf_pos ? {1'b0, {(DW-1){1'b1}}} :
                ovf_neg ? {1'b1, {(DW-1){1'b0}}} :
                          z_ext[DW-1:0];

            always @(posedge clk) begin
                if (!rst_n) z[j] <= {DW{1'b0}};
                else if (en) z[j] <= z_sat;
            end
        end
    endgenerate

endmodule

`default_nettype wire
