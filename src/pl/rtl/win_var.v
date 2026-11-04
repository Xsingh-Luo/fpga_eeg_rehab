//=============================================================================
// win_var.v —— M6：滑动窗方差（决策更新率 250 Hz 的关键）
//=============================================================================
// 对应设计文档：规划/02 算法实现规格书.md §4.5
//
// 公式：
//     窗口内 T 个样本     Sz  = Σ z[n]
//                        Sz2 = Σ z[n]^2
//     方差               var = (Sz2 − Sz²/T) / T
//
// ★ 核心技巧：**加新减旧**（running sum）
//     Sz  ← Sz  + z[n] − z[n−T]
//     Sz2 ← Sz2 + z[n]² − z[n−T]²
//   每来一个采样点只需要 2 次加、1 次减、1 次平方 —— **O(1)**，与窗口长度无关。
//
//   如果不用这个技巧：每拍重算 T=626 个样本 → 决策更新率从 250 Hz 掉到
//   250/626 ≈ 0.4 Hz，整个"每 4 ms 一个决策"的卖点就没了。
//
// ★ 为什么用整数累加
//   加新减旧在无限精度下精确；用整数表示就没有舍入误差 → **理论零漂移**。
//   若用浮点，每拍两次舍入，626 点窗跑几小时后 Sz 会缓慢漂走。
//
// ★★ 关于存储结构的两次返工（见规划/10 §4）
//   第一版用 `reg [DW-1:0] ring [0:N_COMP*WIN_LEN-1]` 扁平数组 + 组合读，
//   仿真完全正确，但 **Vivado 推断不出 BRAM**：
//     [Synth 8-3391] Unable to infer a block/distributed RAM for 'ring_reg'
//     ... the number of bits (90144) is too large [to dissolve into bits]
//   原因是「同址读写 + 扁平索引 + 组合读」不符合 BRAM 模板。
//   现改为 **二维数组 + 读地址打拍 + 输出寄存**（标准简单双口 BRAM 模板）。
//
//   代价是每采样点要 3 个时钟（读地址 / 读数据 / 累加）。
//   采样间隔是 400,000 个时钟，**3 拍完全可以忽略** ——
//   用"多花几拍"换"能推断出 BRAM、能综合"，这笔账在本项目里永远划算。
//
// 定点格式：
//     z     : Q10.13 / 24 bit
//     z²    : Q20.26 / 48 bit
//     Sz    : 48 bit 有符号（可正可负！下游必须符号扩展）
//     Sz2   : 48 bit 有符号（恒非负）
//
// 存储：N_COMP × WIN_LEN × 24 bit。6 × 626 × 24 = 90 kbit → 3 个 RAMB36。
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module win_var #(
    parameter integer N_COMP  = 6,
    parameter integer DW      = 24,
    parameter integer QZ      = 13,
    parameter integer AW      = 48,
    parameter integer WIN_LEN = 626,
    parameter integer ADDR_W  = 10
) (
    input  wire                     clk,
    input  wire                     rst_n,
    input  wire                     en,                 // 250 Hz 节拍（单拍脉冲）
    input  wire                     clear,              // 软复位
    input  wire signed [DW-1:0]     z  [0:N_COMP-1],    // Q10.13
    output reg  signed [AW-1:0]     sz [0:N_COMP-1],    // Σz
    output reg  signed [AW-1:0]     sz2[0:N_COMP-1],    // Σz²
    output reg                      win_full
);

    // ── 环形缓冲：**每个分量一份独立存储器** ────────────────
    //
    // ★★ 为什么不能用一个二维数组 ring[N_COMP][WIN_LEN]
    //   仿真完全正确，但 Vivado 报
    //     [Synth 8-3391] Unable to infer a block/distributed RAM for 'ring_reg'
    //     ... the number of bits (90144) is too large [to dissolve into bits]
    //   结果 1728 个 LUT 被当分布式 RAM 用掉（LUT as Memory 9.93%），
    //   而 Block RAM 只用了 0.5 个 tile。
    //
    //   根因：**一个数组要同时提供 N_COMP 个读口 + N_COMP 个写口**，
    //   而 BRAM 物理上只有 1~2 个口。拆成 N_COMP 个独立存储器后，
    //   每个都是标准的**简单双口**（1 写 + 1 读），就能各自推断成 RAMB18。
    //
    //   容量：626 × 24 bit = 15 kbit → 每个分量一个 RAMB18（18 kbit），
    //   6 个分量 = 6 个 RAMB18 = 3 个 RAMB36。
    //
    // ★★ 读必须和写**同一拍、同一地址**（read-first 语义）：
    //   第一版把读放在写的下一拍，那时 mem[wp] 已被新值覆盖，
    //   读回来的是 z[n] 自己 → sz += z[n] − z[n] = 不变。
    //   现象极其隐蔽：Sz 恰好等于**上一个采样点**的值
    //   （逐行比对会发现"RTL 第 i 行 == 参考第 i−1 行"）。
    reg [ADDR_W-1:0]    wp;         // 写指针
    reg [15:0]          fill_cnt;

    reg signed [DW-1:0] z_d   [0:N_COMP-1];   // z 打一拍，与 z_old 对齐
    reg signed [DW-1:0] z_old [0:N_COMP-1];
    reg                 sub_en;                // 本拍是否需要"减旧"

    genvar gk;
    generate
        for (gk = 0; gk < N_COMP; gk = gk + 1) begin : g_mem
            (* ram_style = "block" *)
            reg signed [DW-1:0] mem [0:WIN_LEN-1];

            always @(posedge clk) begin
                if (!rst_n || clear) begin
                    z_d[gk]   <= {DW{1'b0}};
                    z_old[gk] <= {DW{1'b0}};
                end else if (en) begin
                    mem[wp]   <= z[gk];       // 写新
                    z_old[gk] <= mem[wp];     // ★ 同址同沿 → read-first 得旧值
                    z_d[gk]   <= z[gk];
                end
            end
        end
    endgenerate

    reg acc_ph;

    integer k;

    always @(posedge clk) begin
        if (!rst_n || clear) begin
            acc_ph   <= 1'b0;
            wp       <= {ADDR_W{1'b0}};
            fill_cnt <= 16'd0;
            sub_en   <= 1'b0;
            win_full <= 1'b0;
            // ★ z_d / z_old 的驱动与复位都在上面的 g_mem 里，
            //   这里千万不能再赋值 —— 否则多驱动。
            for (k = 0; k < N_COMP; k = k + 1) begin
                sz[k]    <= {AW{1'b0}};
                sz2[k]   <= {AW{1'b0}};
            end
        end else begin
            acc_ph <= 1'b0;

            // ① 写指针 / 填充计数（存储访问在 g_mem 的 always 里）
            if (en) begin
                sub_en <= win_full;
                wp     <= (wp == WIN_LEN[ADDR_W-1:0] - 1'b1)
                          ? {ADDR_W{1'b0}} : (wp + 1'b1);
                if (fill_cnt < WIN_LEN[15:0])
                    fill_cnt <= fill_cnt + 16'd1;
                if (fill_cnt >= WIN_LEN[15:0] - 16'd1)
                    win_full <= 1'b1;
                acc_ph <= 1'b1;                   // 下一拍做累加
            end

            // ② 加新减旧
            if (acc_ph) begin
                for (k = 0; k < N_COMP; k = k + 1) begin
                    if (sub_en) begin
                        sz[k]  <= sz[k]  + z_d[k] - z_old[k];
                        sz2[k] <= sz2[k] + z_d[k] * z_d[k]
                                        - z_old[k] * z_old[k];
                    end else begin
                        sz[k]  <= sz[k]  + z_d[k];
                        sz2[k] <= sz2[k] + z_d[k] * z_d[k];
                    end
                end
            end
        end
    end

endmodule

`default_nettype wire
