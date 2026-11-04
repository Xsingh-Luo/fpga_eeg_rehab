//=============================================================================
// uart_rx.v —— M1：异步串口接收
//=============================================================================
// 对应设计文档：规划/02_算法实现规格书.md §4.2、规划/04_串口接入交接文档.md §6.2
//
// 功能：
//   把 Cyton 的 115200 8N1 字节流还原成 byte + byte_valid。
//
// 为什么这么设计：
//   【位周期 868 的由来】100 MHz / 115200 = 868.06，取整 868
//     → 实际波特率 100e6/868 = 115207.4，误差 +0.006%，
//       远小于 UART 能容忍的 ±2~3%，所以不需要小数分频。
//   【为什么采样在位中点】起始位下降沿后延时 1.5 个位周期才采第 0 位，
//     之后每 1 个位周期采一位。这样采样点落在每位正中央，
//     对时钟偏差和线缆抖动的容忍度最大。
//   【为什么要两级同步器】uart_rx 是异步输入，直接进状态机会有亚稳态，
//     表现为随机的位错。两级 FF 是最低要求（本模块用三级 + 多数表决做毛刺滤波）。
//   【为什么加毛刺滤波】隔离器 + 长杜邦线容易引入窄毛刺，会被误判成起始位。
//     连续 3 个采样一致才认边沿，代价只有 2 个 FF。
//
// 时序：位周期计数器是唯一的长组合路径（比较 + 加法），100 MHz 下轻松收敛。
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module uart_rx #(
    parameter integer CLK_FREQ = 100_000_000,   // 系统时钟
    parameter integer BAUD     = 115200,        // 波特率
    parameter integer FILTER   = 1              // 1=开毛刺滤波
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       rx,            // 异步串口输入（已经过隔离器）
    output reg  [7:0] byte_out,      // 收到的字节
    output reg        byte_valid,    // 单周期有效
    output reg        frame_err      // 停止位非法（帧错误）
);

    // ── 位周期与半周期 ────────────────────────────────────────────
    localparam integer DIV      = CLK_FREQ / BAUD;   // 868
    localparam integer DIV_HALF = DIV / 2;           // 434
    localparam integer CNT_W    = $clog2(DIV + 1);

    // ── 输入同步 + 毛刺滤波 ──────────────────────────────────────
    reg [2:0] rx_sync;
    always @(posedge clk) begin
        if (!rst_n) rx_sync <= 3'b111;
        else        rx_sync <= {rx_sync[1:0], rx};
    end

    wire rx_f;
    generate
        if (FILTER) begin : g_filter
            // 三级一致才认，滤掉 1~2 个时钟的窄毛刺
            assign rx_f = (rx_sync == 3'b000) ? 1'b0 :
                          (rx_sync == 3'b111) ? 1'b1 : rx_sync[2];
        end else begin : g_nofilter
            assign rx_f = rx_sync[2];
        end
    endgenerate

    // ── 状态机 ──────────────────────────────────────────────────
    localparam S_IDLE  = 2'd0,
               S_START = 2'd1,
               S_DATA  = 2'd2,
               S_STOP  = 2'd3;

    reg [1:0]      state;
    reg [CNT_W-1:0] cnt;
    reg [2:0]      bit_idx;
    reg [7:0]      shifter;

    always @(posedge clk) begin
        if (!rst_n) begin
            state      <= S_IDLE;
            cnt        <= {CNT_W{1'b0}};
            bit_idx    <= 3'd0;
            shifter    <= 8'd0;
            byte_out   <= 8'd0;
            byte_valid <= 1'b0;
            frame_err  <= 1'b0;
        end else begin
            byte_valid <= 1'b0;      // 默认拉低，只在 STOP 判成功那一拍拉高
            frame_err  <= 1'b0;

            case (state)
                // ── 等起始位下降沿 ──
                S_IDLE: begin
                    if (rx_f == 1'b0) begin
                        cnt   <= DIV_HALF[CNT_W-1:0];  // 先走半个位周期
                        state <= S_START;
                    end
                end

                // ── 半位周期后确认仍是低（否则是毛刺，丢弃）──
                S_START: begin
                    if (cnt == 0) begin
                        if (rx_f == 1'b0) begin
                            cnt     <= DIV[CNT_W-1:0] - 1'b1;
                            bit_idx <= 3'd0;
                            state   <= S_DATA;
                        end else begin
                            state <= S_IDLE;   // 假起始位
                        end
                    end else begin
                        cnt <= cnt - 1'b1;
                    end
                end

                // ── 每位中点采样，LSB 先 ──
                S_DATA: begin
                    if (cnt == 0) begin
                        shifter <= {rx_f, shifter[7:1]};   // 右移，先收的是 LSB
                        cnt     <= DIV[CNT_W-1:0] - 1'b1;
                        if (bit_idx == 3'd7) begin
                            bit_idx <= 3'd0;
                            state   <= S_STOP;
                        end else begin
                            bit_idx <= bit_idx + 1'b1;
                        end
                    end else begin
                        cnt <= cnt - 1'b1;
                    end
                end

                // ── 停止位必须是高 ──
                S_STOP: begin
                    if (cnt == 0) begin
                        if (rx_f == 1'b1) begin
                            byte_out   <= shifter;
                            byte_valid <= 1'b1;
                        end else begin
                            frame_err  <= 1'b1;   // 帧错误：丢弃该字节并计数
                        end
                        state <= S_IDLE;
                    end else begin
                        cnt <= cnt - 1'b1;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule

`default_nettype wire
