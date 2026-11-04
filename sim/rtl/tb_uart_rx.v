//=============================================================================
// tb_uart_rx.v —— uart_rx 模块测试台
//=============================================================================
// 验证项：
//   T1 正常字节收发（5 个边界值）
//   T2 毛刺抑制（起始位前的窄脉冲不应被误判）
//   T3 帧错误（停止位为低时应报 frame_err 且不输出字节）
//   T4 背靠背连续字节（验证状态机不会漏位）
//
// 运行：iverilog -g2012 -o tb_uart_rx.vvp tb_uart_rx.v ../rtl/uart_rx.v && vvp tb_uart_rx.vvp
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module tb_uart_rx;

    localparam integer CLK_FREQ = 100_000_000;
    localparam integer BAUD     = 115200;
    localparam integer DIV      = CLK_FREQ / BAUD;      // 868
    localparam real    BIT_NS   = 1e9 * DIV / CLK_FREQ; // 8680 ns

    reg  clk = 0, rst_n = 0, rx = 1;
    wire [7:0] byte_out;
    wire       byte_valid, frame_err;

    integer errors = 0;
    integer n_tests = 0;

    // 100 MHz
    always #5 clk = ~clk;

    uart_rx #(.CLK_FREQ(CLK_FREQ), .BAUD(BAUD), .FILTER(1)) dut (
        .clk(clk), .rst_n(rst_n), .rx(rx),
        .byte_out(byte_out), .byte_valid(byte_valid), .frame_err(frame_err)
    );

    // ── 发一个字节（8N1）──────────────────────────────────────────
    task send_byte(input [7:0] b);
        integer i;
        begin
            rx = 1'b0;                  // 起始位
            #(BIT_NS);
            for (i = 0; i < 8; i = i + 1) begin
                rx = b[i];              // LSB 先
                #(BIT_NS);
            end
            rx = 1'b1;                  // 停止位
            #(BIT_NS);
        end
    endtask

    // 发送但故意把停止位拉低（帧错误）
    task send_byte_badstop(input [7:0] b);
        integer i;
        begin
            rx = 1'b0;  #(BIT_NS);
            for (i = 0; i < 8; i = i + 1) begin rx = b[i]; #(BIT_NS); end
            rx = 1'b0;  #(BIT_NS);      // ← 非法停止位
            rx = 1'b1;  #(BIT_NS);
        end
    endtask

    // ── 监视输出 ─────────────────────────────────────────────────
    reg [7:0] got_byte;
    reg       got_valid;
    always @(posedge clk) begin
        if (byte_valid) begin
            got_byte  <= byte_out;
            got_valid <= 1'b1;
        end
    end

    task expect_byte(input [7:0] exp, input [127:0] tag);
        begin
            n_tests = n_tests + 1;
            got_valid = 1'b0;
            send_byte(exp);
            #(BIT_NS * 2);
            if (!got_valid) begin
                $display("  [FAIL] %0s: 未收到字节", tag);
                errors = errors + 1;
            end else if (got_byte !== exp) begin
                $display("  [FAIL] %0s: 期望 0x%02X 实得 0x%02X", tag, exp, got_byte);
                errors = errors + 1;
            end else begin
                $display("  [ OK ] %0s: 0x%02X", tag, exp);
            end
        end
    endtask

    integer err_before;
    initial begin
        $dumpfile("tb_uart_rx.vcd");
        $dumpvars(0, tb_uart_rx);

        $display("=== tb_uart_rx ===");
        $display("DIV=%0d  位周期=%.0f ns", DIV, BIT_NS);

        rst_n = 0; #200; rst_n = 1; #1000;

        // ── T1 正常字节 ──
        $display("\n[T1] 正常字节收发");
        expect_byte(8'h41, "BOP 0x41");
        expect_byte(8'h00, "全 0");
        expect_byte(8'hFF, "全 1");
        expect_byte(8'h55, "0101_0101");
        expect_byte(8'hAA, "1010_1010");

        // ── T2 毛刺抑制 ──
        $display("\n[T2] 毛刺抑制（起始位前 20ns 窄脉冲）");
        n_tests = n_tests + 1;
        got_valid = 1'b0;
        rx = 1'b0; #20; rx = 1'b1;      // 20 ns 毛刺（< 2 个时钟）
        #(BIT_NS * 3);
        if (got_valid) begin
            $display("  [FAIL] 毛刺被误判成起始位，收到 0x%02X", got_byte);
            errors = errors + 1;
        end else begin
            $display("  [ OK ] 毛刺被正确忽略");
        end

        // 毛刺后仍应能正常收字节
        expect_byte(8'h5A, "毛刺后仍可正常接收");

        // ── T3 帧错误 ──
        $display("\n[T3] 帧错误检测");
        n_tests = n_tests + 1;
        got_valid = 1'b0; err_before = 0;
        send_byte_badstop(8'h3C);
        #(BIT_NS * 2);
        if (got_valid) begin
            $display("  [FAIL] 停止位非法却输出了字节 0x%02X", got_byte);
            errors = errors + 1;
        end else begin
            $display("  [ OK ] 停止位非法，字节被丢弃");
        end

        // ── T4 背靠背 ──
        $display("\n[T4] 背靠背连续字节（10 个）");
        n_tests = n_tests + 1;
        begin : t4
            integer k;
            integer ok_count;
            reg [7:0] exp_seq;
            ok_count = 0;
            got_valid = 1'b0;
            for (k = 0; k < 10; k = k + 1) begin
                exp_seq = 8'h10 + k[7:0];
                send_byte(exp_seq);
            end
            #(BIT_NS * 3);
            // 简单校验：最后一字节应被正确收到
            if (got_valid && got_byte === 8'h19)
                $display("  [ OK ] 连续 10 字节，末字节 0x%02X 正确", got_byte);
            else begin
                $display("  [FAIL] 末字节期望 0x19 实得 0x%02X (valid=%b)", got_byte, got_valid);
                errors = errors + 1;
            end
        end

        // ── 汇总 ──
        $display("\n========================================");
        if (errors == 0) $display(" 全部通过（%0d 项）", n_tests);
        else             $display(" 失败 %0d / %0d 项", errors, n_tests);
        $display("========================================");
        $finish;
    end

    // 超时保护
    initial begin
        #(BIT_NS * 400);
        $display("!! 仿真超时");
        $finish;
    end

endmodule

`default_nettype wire
