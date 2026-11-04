//=============================================================================
// dc_block.v —— M3：去直流（一阶高通）
//=============================================================================
// 对应设计文档：规划/02 算法实现规格书.md §2.2
//
// 公式（一阶高通，等效于「减滑动均值」的递归实现）：
//     y[n] = a · ( y[n-1] + x[n] − x[n-1] )
//     a    = 1 / (1 + 2π·fc/fs),  fc = 0.5 Hz, fs = 250 Hz
//          = 1 / (1 + 2π·0.5/250) = 0.98760
//
// ★ 为什么必须做
//   ADS1299 有数百 mV 级直流偏移，而 EEG 只有 ±100 µV，动态范围比 ~2000:1。
//   不去 DC 就得白白多留 11 bit 位宽 —— 那 11 bit 会一路吃掉 DSP 的余量。
//
// 定点格式：
//     x     : Q8.15 / 24 bit（计数域）
//     a     : Q1.15 / 16 bit = 32361（a = 0.9876，Q1.15 下余量仅 1%）
//     y     : Q8.15 / 24 bit
//
// ⚠️ 系数余量只有 1%（32361 / 32767）。这是设计使然（a 本就贴近 1.0），
//    不是 bug；但**改动截止频率或采样率时必须重新检查是否溢出**。
//
// 状态：x[n-1] 与 y[n-1] 各一份（每通道）。
//   注意 y 用 48 位保存中间精度，避免低位反复截断累积成直流漂移。
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module dc_block #(
    parameter integer DW = 24,      // 数据位宽
    parameter integer CW = 16,      // 系数字宽
    parameter integer Q  = 15,      // 系数小数位（Q1.15）
    parameter integer AW = 32       // 累加器位宽
) (
    input  wire                  clk,
    input  wire                  rst_n,
    input  wire                  en,               // 采样使能
    input  wire                  en_mul,           // ★ 乘法级使能（比 en 晚一拍）
    input  wire signed [DW-1:0]  x,                // Q8.15
    input  wire signed [CW-1:0]  a,                // Q1.15，默认 32361
    output reg  signed [DW-1:0]  y                 // Q8.15
);

    // ★★ 两处针对时序的改动（综合后 WNS = −3.169 ns 时最差路径就在本模块）
    //
    //  改动 1：累加器 48 → 32 位
    //    原路径：g_dc[2]/x_prev_reg[1] → ...psdsp_6/D
    //            Data Path 12.995 ns，Logic Levels 16（**CARRY4 = 10**）
    //    `inner = y_acc + x − x_prev` 是 48 位三级串行加减，进位链 10 级。
    //    信号本身是 Q8.15（24 位），留 8 位余量足够，
    //    32 位把进位链砍到约 7 级。
    //
    //  改动 2：把「加减」与「乘法」切成两级流水
    //    原来一拍里同时做 48 位加减 + 16×32 乘法 + 移位，
    //    现在：
    //      级 1（en）    ：inner_r <= y_acc + x − x_prev
    //      级 2（en_mul）：y_acc <= (a · inner_r) >>> Q
    //    代价是本模块延迟从 1 拍变 2 拍。
    //    采样间隔 400,000 拍，**这 1 拍完全无所谓**。
    //    ⚠️ 调用方必须把下游使能再往后挪一拍（见 eeg_csp_svm_top 的使能流水线）。
    reg signed [DW-1:0] x_prev;
    reg signed [AW-1:0] y_acc;      // 高精度反馈，只在输出口截断
    reg signed [AW-1:0] inner_r;    // 级 1 输出

    wire signed [AW-1:0] inner = y_acc + {{(AW-DW){x[DW-1]}}, x}
                                        - {{(AW-DW){x_prev[DW-1]}}, x_prev};

    wire signed [63:0]   acc   = a * inner_r;       // Q1.15 × Q8.15 → Q9.30
    wire signed [63:0]   y_ext = acc >>> Q;         // → Q8.15（保留高精度）

    // ★★ 饱和检测只看高位，**不要做 64 位比较**
    //   原来写 (y_ext > 24 位上限) 这种比较，综合出来是 64 位减法器 ——
    //   实测最差路径就是它：
    //     g_dc[0].u_dc/acc/CLK → u_bank/.../u_s0/s10/A[10]
    //     Data Path 10.141 ns，Logic Levels 9（**CARRY4 = 6**），WNS = −0.661 ns
    //   改成对 y_ext[62 : DW-1] 做归约（与 svm_core 同一手法）：
    //     正溢出：y_ext 为正 且 除符号位外还有高位为 1
    //     负溢出：y_ext 为负 且 除符号位外并非全 1
    wire ovf_pos = ~y_ext[63] &  (|y_ext[62 : DW-1]);
    wire ovf_neg =  y_ext[63] & ~(&y_ext[62 : DW-1]);
    wire signed [DW-1:0] y_sat =
        ovf_pos ? {1'b0, {(DW-1){1'b1}}} :
        ovf_neg ? {1'b1, {(DW-1){1'b0}}} :
                  y_ext[DW-1:0];

    always @(posedge clk) begin
        if (!rst_n) begin
            x_prev  <= {DW{1'b0}};
            inner_r <= {AW{1'b0}};
            y_acc   <= {AW{1'b0}};
            y       <= {DW{1'b0}};
        end else begin
            // 级 1：加减
            if (en) begin
                x_prev  <= x;
                inner_r <= inner;
            end
            // 级 2：乘法 + 移位（反馈用未截断的高精度值，避免极限环）
            if (en_mul) begin
                y_acc <= y_ext[AW-1:0];
                y     <= y_sat;
            end
        end
    end

endmodule

`default_nettype wire
