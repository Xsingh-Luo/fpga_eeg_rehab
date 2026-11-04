//=============================================================================
// pkt_parser.v —— M2：Cyton 33 字节包解析
//=============================================================================
// 对应设计文档：规划/04_串口接入交接文档.md §6.3
//
// 功能：
//   从字节流中同步出 Cyton 的 33 字节包，拆出 8 个通道的 24 位样本。
//
// 包格式（取自固件源码 OpenBCI_32bit_Library.cpp，非二手资料）：
//   [0]      0x41 = OPENBCI_BOP        ('A')
//   [1]      sampleCounter 0~255 回绕
//   [2..25]  8 通道 x 3 字节，24 位有符号，大端（MSB 在前）
//   [26..31] AUX 6 字节（默认是加速度计，本设计忽略）
//   [32]     0xC0 | packetType         校验 (b & 0xF0) == 0xC0
//
// 为什么这么设计：
//   【不缓存整包】只用一个 6 位索引 + 8 个 24 位通道寄存器，边收边按索引
//     塞进对应通道。省掉 33 字节缓冲 → 0 BRAM / 0 LUTRAM。
//   【失步恢复靠三重判断】0x41 也可能出现在数据里，只搜同步字会误锁。
//     所以必须"33 字节定长 + 包尾校验 + 字节间隔超时"三者同时成立：
//       - 收满 33 字节且包尾合法 → 输出样本
//       - 字节间隔 > TIMEOUT 个周期 → 判定断流，回 IDLE 重新同步
//     超时阈值取 2 个字节时间（2 x 8680 = 17360 周期 ≈ 174 µs）。
//     依据：33 字节是**连续突发**发送的（2.864 ms 发完，然后空闲 1.136 ms），
//     包内不会出现 >2 字节的空隙，所以这个阈值既能快速恢复又不会误判。
//   【丢包检测靠序号】seq_new != seq_prev+1 → 累加丢失数。
//     注意序号只有 1 字节，一次丢超过 255 个样本就无法识别——这是协议限制。
//
// 时序：本模块全是窄组合逻辑（索引比较 + 移位），100 MHz 下无压力。
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module pkt_parser #(
    parameter integer N_CH      = 8,       // 通道数（Cyton 固定 8）
    parameter integer CLK_FREQ  = 100_000_000,
    parameter integer BAUD      = 115200,
    parameter integer TOUT_BYTE = 2        // 字节间隔超时 = 几个字节时间
) (
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire [7:0]              byte_in,
    input  wire                    byte_valid,
    input  wire                    frame_err,      // 来自 uart_rx

    output reg                     sample_valid,   // 单周期，样本就绪
    output reg  signed [23:0]      ch_data  [0:N_CH-1],
    output reg  [7:0]              sample_seq,
    output reg  [3:0]              pkt_type,

    // 错误计数（供 AXI-Lite 读取）
    output reg  [7:0]              cnt_footer_err,
    output reg  [7:0]              cnt_sync_err,
    output reg  [7:0]              cnt_drop,
    output reg  [7:0]              cnt_frame_err,
    output reg  [31:0]             cnt_pkt         // 累计合法包数
);

    // ★ 注意单位：CLK_FREQ/BAUD 是 **1 个位** 的周期数（868），
    //   而 1 个字节 = 起始位 + 8 数据位 + 停止位 = **10 个位** = 8680 周期。
    //   第一版把位周期当成了字节周期，导致超时阈值只有 17.4 µs，
    //   而实际字节间隔是 86.8 µs —— 每收一个字节就误判断流。
    //   仿真抓到：收到 0x41 后 17.36 µs 触发 sync_err，回 IDLE，整包丢失。
    localparam integer BIT_TICKS  = CLK_FREQ / BAUD;              // 868
    localparam integer BYTE_TICKS = 10 * BIT_TICKS;               // 8680
    localparam integer TIMEOUT    = BYTE_TICKS * TOUT_BYTE;       // 17360 ≈ 174 µs
    localparam integer TOUT_W     = $clog2(TIMEOUT + 1);

    localparam [7:0] BOP      = 8'h41;   // OPENBCI_BOP
    localparam [7:0] FOOT_MSK = 8'hF0;
    localparam [7:0] FOOT_VAL = 8'hC0;   // PCKT_END

    localparam S_IDLE = 1'b0,
               S_RECV = 1'b1;

    reg              state;
    reg [5:0]        idx;          // 0..32
    reg [TOUT_W-1:0] tout;
    reg [7:0]        seq_prev;
    reg              seq_valid;

    integer c;
    always @(posedge clk) begin
        if (!rst_n) begin
            state          <= S_IDLE;
            idx            <= 6'd0;
            tout           <= {TOUT_W{1'b0}};
            sample_valid   <= 1'b0;
            sample_seq     <= 8'd0;
            pkt_type       <= 4'd0;
            seq_prev       <= 8'd0;
            seq_valid      <= 1'b0;
            cnt_footer_err <= 8'd0;
            cnt_sync_err   <= 8'd0;
            cnt_drop       <= 8'd0;
            cnt_frame_err  <= 8'd0;
            cnt_pkt        <= 32'd0;
            for (c = 0; c < N_CH; c = c + 1) ch_data[c] <= 24'sd0;
        end else begin
            sample_valid <= 1'b0;

            if (frame_err) cnt_frame_err <= cnt_frame_err + 8'd1;

            // ── 字节间隔超时 ──
            // ★ 坑：tout 必须在**进入 S_RECV 时**清零，不能只在 byte_valid 时清。
            //   超时后 tout 停在 TIMEOUT；此时若只在 byte_valid 且 state==S_RECV 时清零，
            //   重新锁上 0x41 的那一拍 state 还是 IDLE（非阻塞赋值下一拍才变），
            //   tout 没被清 → 下一拍立刻又判超时 → **此后每个包都瞬间超时，永远收不到数据**。
            //   现象：第一个超时之后 cnt_pkt 再也不增长，sync_err 却一直涨。
            if (state == S_RECV) begin
                if (byte_valid) tout <= {TOUT_W{1'b0}};
                else if (tout == TIMEOUT[TOUT_W-1:0]) begin
                    state        <= S_IDLE;
                    tout         <= {TOUT_W{1'b0}};
                    cnt_sync_err <= cnt_sync_err + 8'd1;
                end else tout <= tout + 1'b1;
            end

            if (byte_valid) begin
                case (state)
                    // ── 搜同步字 ──
                    S_IDLE: begin
                        if (byte_in == BOP) begin
                            idx   <= 6'd1;
                            tout  <= {TOUT_W{1'b0}};   // ★ 必须在这里清零，理由见上
                            state <= S_RECV;
                        end
                    end

                    // ── 按索引收集 ──
                    S_RECV: begin
                        // 通道 c 的 3 字节落在 idx = 2+3c .. 4+3c
                        if (idx >= 6'd2 && idx <= 6'd25) begin
                            c = (idx - 6'd2) / 3;              // 通道号
                            case ((idx - 6'd2) % 3)
                                0: ch_data[c][23:16] <= byte_in;  // 大端：MSB 先到
                                1: ch_data[c][15:8]  <= byte_in;
                                2: ch_data[c][7:0]   <= byte_in;
                            endcase
                        end
                        else if (idx == 6'd1) sample_seq <= byte_in;

                        if (idx == 6'd32) begin
                            // 第 33 字节 = 包尾，校验高 4 位
                            if ((byte_in & FOOT_MSK) == FOOT_VAL) begin
                                pkt_type     <= byte_in[3:0];
                                sample_valid <= 1'b1;
                                cnt_pkt      <= cnt_pkt + 32'd1;
                                // ── 序号连续性检查 ──
                                if (seq_valid && (sample_seq != (seq_prev + 8'd1)))
                                    cnt_drop <= cnt_drop + (sample_seq - seq_prev - 8'd1);
                            end else begin
                                cnt_footer_err <= cnt_footer_err + 8'd1;
                            end
                            seq_prev  <= sample_seq;
                            seq_valid <= 1'b1;
                            state     <= S_IDLE;
                            idx       <= 6'd0;
                        end else begin
                            idx <= idx + 6'd1;
                        end
                    end
                endcase
            end
        end
    end

endmodule

`default_nettype wire
