//=============================================================================
// clk_pll.v —— 100 MHz → 95 MHz 锁相环 + 锁定指示
//=============================================================================
// 对应文档：规划/10_调试报告.md §4.5（收尾方案）
//
// ★ 为什么要在 PL 里再做一个 PLL，而不是直接把 PS 的 FCLK_CLK0 配成 95 MHz
//   两者电气上等价，但 PL 侧的 MMCM/PLL 有两个无法替代的好处：
//     ① 它给出 **locked** 信号 —— 可以当系统复位用（用户明确要求）
//     ② 时钟参数写在 RTL 里，**换板子不用改 PS7 的 IP 配置**
//        （本项目的硬约束是 ZYNQ7020-484 与 PYNQ-Z2 双板兼容）
//
// ★ 为什么用 MMCME2_BASE 原语，而不是 clk_wiz IP
//   功能完全一样（clk_wiz 本身就是 MMCME2_BASE 的封装），但：
//     - 不需要生成 IP、不涉及 IP 版本升级
//     - **可以用 iverilog 仿真**（`SIMULATION` 下走行为模型）
//   本项目的主力验证手段是 iverilog 全流程仿真，
//   换成 clk_wiz 会让仿真链断掉，代价远大于收益。
//
// ── 频率规划 ──────────────────────────────────────────────────
//   VCO = CLKIN × MULT / DIVCLK = 100 × 19 / 2 = 950 MHz
//   CLKOUT = VCO / DIVOUT = 950 / 10 = 95.0 MHz
//
//   各项约束（Artix-7 -2）：
//     VCO            600 ~ 1440 MHz   → 950 ✓
//     PFD = CLKIN/D  10  ~ 500 MHz    → 50  ✓
//     MULT           2   ~ 64         → 19  ✓
//     DIVOUT         1   ~ 128        → 10  ✓
//
// ── 95 MHz 对 UART 的影响 ─────────────────────────────────────
//   DIV = 95000000 / 115200 = 824（整数除法）
//   实际波特率 = 95000000 / 824 = 115291 → 误差 **+0.079%**
//   要求是 ±2%，余量 25 倍。**所以 95 MHz 不是妥协，是等价方案。**
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module clk_pll #(
    parameter real    CLKIN_PERIOD = 10.0,   // 输入 100 MHz
    parameter integer MULT         = 19,     // VCO = 100 × 19 / 2 = 950 MHz
    parameter integer DIVCLK       = 2,
    parameter integer DIVOUT       = 10      // 950 / 10 = 95 MHz
) (
    input  wire clk_in,      // 来自 PS 的 FCLK_CLK0 = 100 MHz
    input  wire rst_in,      // 可选复位（一般接 1'b0）
    output wire clk_out,     // 95 MHz
    output wire locked       // 锁定指示：低 = 未锁定
);

`ifdef SIMULATION
    // ─────────────────────────────────────────────────────────
    //  行为模型（iverilog 用）
    //
    //  仿真里时钟是**等比缩小**的（见 tb_fullflow.v 的 CLK_FREQ/BAUD），
    //  所以这里不真的做 100→95 分频，而是**透传**并模拟锁定过程：
    //  上电后 16 拍拉高 locked，让下游的复位同步器产生一个复位脉冲。
    //  这样仿真验证的是"锁定 → 复位释放"的控制流，而不是分频比
    //  （分频比由综合时的真实 MMCM 参数保证，见文件头）。
    // ─────────────────────────────────────────────────────────
    reg [3:0] lock_cnt;
    initial lock_cnt = 4'd0;
    always @(posedge clk_in) begin
        if (rst_in)                    lock_cnt <= 4'd0;
        else if (lock_cnt != 4'hF)     lock_cnt <= lock_cnt + 4'd1;
    end

    assign clk_out = clk_in;
    assign locked  = (lock_cnt == 4'hF);

`else
    // ─────────────────────────────────────────────────────────
    //  真实实现（Vivado 综合用）
    // ─────────────────────────────────────────────────────────
    wire clkfb;

    MMCME2_BASE #(
        .BANDWIDTH          ("OPTIMIZED"),
        .CLKFBOUT_MULT_F    (MULT),
        .CLKFBOUT_PHASE     (0.0),
        .CLKIN1_PERIOD      (CLKIN_PERIOD),
        .CLKOUT0_DIVIDE_F   (DIVOUT),
        .CLKOUT0_DUTY_CYCLE (0.5),
        .CLKOUT0_PHASE      (0.0),
        .DIVCLK_DIVIDE      (DIVCLK),
        .REF_JITTER1        (0.0),
        .STARTUP_WAIT       ("FALSE")
    ) u_mmcm (
        .CLKOUT0  (clk_out),
        .CLKFBOUT (clkfb),
        .LOCKED   (locked),
        .CLKIN1   (clk_in),
        .PWRDWN   (1'b0),
        .RST      (rst_in),
        .CLKFBIN  (clkfb)
    );

`endif

endmodule

`default_nettype wire
