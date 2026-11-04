//=============================================================================
// eeg_csp_svm_top.v —— PL 顶层：从 UART 字节到决策标签
//=============================================================================
// 对应文档：规划/09_PL与PS设计文档.md §1、§2
//
// 数据流：
//   uart_rx → pkt_parser → dc_block ×N_CH → biquad_bank（3 带 × 2 节）
//          → csp_project ×3 带 → win_var（6 分量滑动窗）
//          → lognorm（6 维特征）→ MIBIF 选 4 维 → svm_core → 标签 + 决策值
//          → axil_regs（PS 读）
//
// ★★ 使能流水线（本模块最关键的时序设计）
//   每一级在 en 沿寄存输出，所以下一级必须用**晚一拍的 en**，
//   否则会读到上一拍的旧数据。这个错误在本次开发中出现过三次
//   （biquad 级联、联合仿真抓取、csp→win_var），见规划/10 §2。
//
//   各级延迟（相对 sample_valid 那一拍）：
//     cycle 0 : sample_valid（pkt_parser 出样本）
//     cycle 1 : en_dc      → dc_block 输出
//     cycle 2 : en_biq     → biquad sec0
//     cycle 3 :              biquad sec1（内部 en_d）
//     cycle 4 : en_csp     → csp_project 输出 z
//     cycle 5 : en_win     → win_var 更新 Sz/Sz2
//     cycle 7 : ln_go      → 启动 lognorm FSM
//     ln_done : en_svm     → svm_core 出决策
//
//   用 8 位移位寄存器实现，一位一级，改延迟只需挪 tap。
//   采样率只有 250 Hz（4 ms = 400,000 拍），这点延迟完全无所谓，
//   换来的好处是**每一级的使能关系一目了然、不会错位**。
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module eeg_csp_svm_top #(
    parameter integer CLK_FREQ  = 100_000_000,
    parameter integer BAUD      = 115200,
    parameter integer N_CH      = 3,
    parameter integer N_BAND    = 3,
    parameter integer N_COMP    = 2,
    parameter integer FEAT_DIM  = 4,
    parameter integer WIN_LEN   = 626,
    parameter integer INPUT_MODE = 0, // 0: legacy UART; 1: PS AXI4-Stream
    parameter LUT_FILE = "log2_lut.hex"
) (
    input  wire        clk_in,       // ★ PS 的 FCLK_CLK0 = 100 MHz（PLL 输入）
    input  wire        rst_n_ext,    // 外部复位（低有效）；可与按钮/软复位并联

    // ── Cyton 串口（经数字隔离器）────────────────────────────
    input  wire        uart_rx,
    output wire        uart_tx,      // 预留：向 Cyton 发 'b'/'s'

    // PS/FIFO sample input. Production AXIS and S_AXI use axis_clk (80 MHz).
    // Connect a proper clock converter when the PS interconnect uses FCLK 100 MHz.
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
    input wire [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *)
    input wire s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *)
    output wire s_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *)
    input wire s_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 axis_clk CLK",
       X_INTERFACE_PARAMETER = "FREQ_HZ 80000000, ASSOCIATED_BUSIF S_AXIS:S_AXI, ASSOCIATED_RESET axis_aresetn" *)
    output wire axis_clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 axis_aresetn RST",
       X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    output wire axis_aresetn,
    output wire [31:0] stream_format_errors,

    // ── AXI4-Lite 从机（PS 控制 / 参数 / 结果）──────────────
    input  wire [31:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    output wire [1:0]  s_axi_bresp,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [31:0] s_axi_araddr,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rvalid,
    input  wire        s_axi_rready,

    // ── 调试/演示 ───────────────────────────────────────────
    output wire        led_pkt,      // 收到合法包的心跳
    output wire        led_label,    // 决策标签
    output wire        dec_valid     // UART: legacy ln_done; AXIS: completed SVM result
);

    assign uart_tx = 1'b1;           // 空闲高（命令通道暂未实现发送）

    // ══════════════════════════════════════════════════════════
    //  ★★ 时钟与复位：PLL（100 → 95 MHz）+ 锁定释放复位
    // ══════════════════════════════════════════════════════════
    //  为什么不再直接用 PS 的 100 MHz：
    //    100 MHz 下综合实现后 WNS = −0.377 ns，时序不收敛。
    //    95 MHz 让 UART 波特率误差从 +0.006% 变为 +0.079%，
    //    **两者都远优于 ±2% 的要求** —— 所以这是等价方案，不是妥协。
    //
    //  ★ 复位用 PLL 的 locked 信号：
    //    上电时 MMCM 输出未稳定，此时**不能**让逻辑开始跑。
    //    用 locked 做异步置位、再用 95 MHz 同步释放（标准的
    //    "异步复位、同步释放"），避免复位撤除时的亚稳态。
    //    4 级同步器给 MMCM 锁定后的时钟留出足够的稳定时间。
    // ══════════════════════════════════════════════════════════
    wire clk_95;
    wire pll_locked;

    // AXIS production: actual800MHz VCO /10 =80MHz. Legacy UART retains95MHz.
    clk_pll #(.CLKIN_PERIOD(10.0), .MULT(INPUT_MODE == 1 ? 16 : 19), .DIVCLK(2), .DIVOUT(10)) u_pll (
        .clk_in  (clk_in),
        .rst_in  (1'b0),
        .clk_out (clk_95),
        .locked  (pll_locked)
    );

    wire arst_n = pll_locked & rst_n_ext;

    reg [3:0] rst_sync;
    always @(posedge clk_95 or negedge arst_n) begin
        if (!arst_n) rst_sync <= 4'b0000;
        else         rst_sync <= {rst_sync[2:0], 1'b1};
    end
    wire rst_n = rst_sync[3];

    // ★ 此后全设计统一用 clk_95 / rst_n。
    //   把内部时钟改名成 clk，下游模块一行都不用改。
    wire clk = clk_95;
    assign axis_clk = clk;
    assign axis_aresetn = rst_n;
    wire ctrl_start, ctrl_soft_reset, ctrl_clear_accum, ctrl_dbg_en;
    wire params_loaded;
    wire core_rst_n = rst_n && ((INPUT_MODE == 0) || !ctrl_soft_reset);
    wire stream_done;
    wire sample_valid;
    reg stream_busy;
    always @(posedge clk) begin
        if (!core_rst_n || !ctrl_start) stream_busy <= 0;
        else if (stream_done) stream_busy <= 0;
        else if (sample_valid) stream_busy <= 1;
    end

    // ══════════════════════════════════════════════════════════
    //  M1 / M2：串口接收与包解析
    // ══════════════════════════════════════════════════════════
    wire [7:0]        byte_out;
    wire              byte_valid, frame_err;
    wire signed [23:0] ch_raw [0:7];
    wire [7:0]        sample_seq;
    wire [3:0]        pkt_type;
    wire [7:0]        cnt_footer, cnt_sync, cnt_drop, cnt_frame;
    wire [31:0]       cnt_pkt;

    generate if (INPUT_MODE == 0) begin : g_uart_input
    assign s_axis_tready = 1'b0;
    assign stream_format_errors = 32'd0;
    uart_rx #(.CLK_FREQ(CLK_FREQ), .BAUD(BAUD)) u_rx (
        .clk(clk), .rst_n(rst_n), .rx(uart_rx),
        .byte_out(byte_out), .byte_valid(byte_valid), .frame_err(frame_err));

    pkt_parser #(.N_CH(8), .CLK_FREQ(CLK_FREQ), .BAUD(BAUD)) u_pp (
        .clk(clk), .rst_n(rst_n),
        .byte_in(byte_out), .byte_valid(byte_valid), .frame_err(frame_err),
        .sample_valid(sample_valid), .ch_data(ch_raw), .sample_seq(sample_seq),
        .pkt_type(pkt_type),
        .cnt_footer_err(cnt_footer), .cnt_sync_err(cnt_sync),
        .cnt_drop(cnt_drop), .cnt_frame_err(cnt_frame), .cnt_pkt(cnt_pkt));
    end else begin : g_stream_input
        wire signed [23:0] assembled [0:N_CH-1];
        wire assembled_valid;
        wire assembled_ready = ctrl_start && params_loaded && !stream_busy;
        reg signed [23:0] held_sample [0:N_CH-1];
        reg [31:0] accepted;
        integer sc;
        axis_sample_input #(.N_CH(N_CH)) u_input (
            .clk(clk), .rst_n(core_rst_n), .enable(ctrl_start && params_loaded),
            .s_axis_tdata(s_axis_tdata), .s_axis_tvalid(s_axis_tvalid),
            .s_axis_tready(s_axis_tready), .s_axis_tlast(s_axis_tlast),
            .sample(assembled), .sample_valid(assembled_valid),
            .sample_ready(assembled_ready), .format_errors(stream_format_errors));
        assign sample_valid = assembled_valid && assembled_ready;
        always @(posedge clk) begin
            if (!core_rst_n) begin
                accepted <= 0;
                for (sc=0; sc<N_CH; sc=sc+1) held_sample[sc] <= 0;
            end else if (sample_valid) begin
                accepted <= accepted + 1'b1;
                for (sc=0; sc<N_CH; sc=sc+1) held_sample[sc] <= assembled[sc];
            end
        end
        for (genvar sch=0; sch<8; sch=sch+1) begin : g_sample
            if (sch<N_CH) assign ch_raw[sch] = held_sample[sch];
            else assign ch_raw[sch] = 24'sd0;
        end
        assign cnt_pkt = accepted;
        assign cnt_sync = stream_format_errors[7:0];
        assign cnt_footer = 0;
        assign cnt_drop = 0;  // No radio sequence is carried by this interface.
        assign cnt_frame = 0;
        assign sample_seq = accepted[7:0];
        assign pkt_type = 0;
        assign byte_out = 0;
        assign byte_valid = 0;
        assign frame_err = 0;
    end endgenerate

    // ══════════════════════════════════════════════════════════
    //  ★ 使能流水线
    // ══════════════════════════════════════════════════════════
    //   ★ win_var 改成 BRAM 友好的 3 拍状态机后，sz 要 4 拍才稳定，
    //     所以 ln_go 必须挪到 seq[8]（原来 seq[6] 只晚 2 拍 → 读到旧值）。
    // ★ max_fanout：WNS 收敛到 +0.003 ns 时，最差路径变成了
    //     seq_reg[5] → u_win/*/CEB2
    //     Data Path 9.549 ns（logic 0.379 + **route 9.170**），Logic Levels = **0**
    //   逻辑级数为 0、布线占 96% —— 这不是逻辑深度问题，
    //   而是**使能网络高扇出**：seq[5] 要驱动 win_var 里所有寄存器的 CE 脚，
    //   物理上分散在芯片各处，一根长线跑 9 ns。
    //   加 max_fanout 让 Vivado 复制这个寄存器，把长线拆成几段短线。
    (* max_fanout = 50 *)
    reg [15:0] seq;
    always @(posedge clk) begin
        if (!core_rst_n)         seq <= 16'h0000;
        else if (sample_valid)   seq <= 16'h0001;
        else if (seq != 16'h0000) seq <= {seq[14:0], 1'b0};
    end

    // ★ dc_block 现在是 2 拍（加减 1 拍 + 乘法 1 拍），所以下游全部后移一拍。
    //   各级延迟合计：
    //     dc  : en_dc  → +1 拍 inner_r → +2 拍 y
    //     biquad: en_biq → sec0 同拍 → sec1 再 +1 → 输出 +2 拍
    //     csp : +1 拍
    //     win_var: en_win → sz 于 +4 拍稳定（BRAM 读写的两拍状态机）
    wire en_dc   = seq[0];
    wire en_dcm  = seq[1];   // dc 的乘法级（比 en_dc 晚一拍）
    wire en_biq  = seq[2];
    wire en_csp  = seq[4];
    wire en_win  = seq[5];
    wire ln_go   = seq[9];   // ★ 必须 ≥ en_win + 4

    // ══════════════════════════════════════════════════════════
    //  M9：AXI-Lite（参数 / 控制 / 结果）—— 先声明，因为下游要用它的参数
    // ══════════════════════════════════════════════════════════
    wire [1:0]         mode;
    wire [15:0]        win_len_cfg;
    wire [7:0]         n_ch_cfg, feat_dim_cfg;
    wire signed [15:0] csp_w  [0:N_BAND*N_CH*N_COMP-1];   // ★ 每带一组
    wire signed [17:0] iir_sos[0:N_BAND*2*5-1];
    wire signed [15:0] svm_w  [0:FEAT_DIM-1];
    wire signed [31:0] svm_b;
    wire signed [31:0] dec_value;
    wire               dec_label;
    wire [31:0]        perf_cnt, acc_cnt;

    axil_regs #(.N_CH(N_CH), .N_COMP(N_COMP), .N_BAND(N_BAND),
                .FEAT_DIM(FEAT_DIM), .WIN_LEN_D(WIN_LEN),
                .CORE_ID((INPUT_MODE == 1) ? 32'h42434931 : 32'h42434930)) u_regs (
        .clk(clk), .rst_n(rst_n),
        .s_axi_awaddr(s_axi_awaddr), .s_axi_awvalid(s_axi_awvalid),
        .s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata), .s_axi_wstrb(s_axi_wstrb),
        .s_axi_wvalid(s_axi_wvalid), .s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp), .s_axi_bvalid(s_axi_bvalid),
        .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr), .s_axi_arvalid(s_axi_arvalid),
        .s_axi_arready(s_axi_arready), .s_axi_rdata(s_axi_rdata),
        .s_axi_rresp(s_axi_rresp), .s_axi_rvalid(s_axi_rvalid),
        .s_axi_rready(s_axi_rready),
        .err_flags({cnt_frame, cnt_drop, cnt_sync, cnt_footer}),
        .acc_cnt(acc_cnt), .perf_cnt(perf_cnt),
        .drop_sync({8'h0, cnt_drop, cnt_sync}),
        .ctrl_start(ctrl_start), .ctrl_soft_reset(ctrl_soft_reset),
        .ctrl_clear_accum(ctrl_clear_accum), .ctrl_dbg_en(ctrl_dbg_en),
        .mode(mode), .win_len_cfg(win_len_cfg), .n_ch_cfg(n_ch_cfg),
        .feat_dim_cfg(feat_dim_cfg),
        .csp_w(csp_w), .iir_sos(iir_sos), .svm_w(svm_w), .svm_b(svm_b),
        .params_loaded(params_loaded),
        .dec_label(dec_label), .dec_value(dec_value));

    // ══════════════════════════════════════════════════════════
    //  M3：去直流（每通道一份）
    // ══════════════════════════════════════════════════════════
    wire signed [23:0] x_dc [0:N_CH-1];
    genvar gc;
    generate
        for (gc = 0; gc < N_CH; gc = gc + 1) begin : g_dc
            dc_block #(.DW(24), .CW(16), .Q(15)) u_dc (
                .clk(clk), .rst_n(core_rst_n), .en(en_dc), .en_mul(en_dcm),
                .x(ch_raw[gc]), .a(16'sd32361), .y(x_dc[gc]));
        end
    endgenerate

    // ══════════════════════════════════════════════════════════
    //  M4：3 子带滤波器组
    // ══════════════════════════════════════════════════════════
    wire signed [23:0] x_band [0:N_CH*N_BAND-1];   // [ch*N_BAND + band]
    biquad_bank #(.N_CH(N_CH), .N_BAND(N_BAND), .DW(24), .CW(18), .Q(14)) u_bank (
        .clk(clk), .rst_n(core_rst_n), .en(en_biq),
        .x(x_dc), .coef(iir_sos), .y(x_band));

    // ══════════════════════════════════════════════════════════
    //  M5：CSP 投影（每带一份）
    // ══════════════════════════════════════════════════════════
    wire signed [23:0] z [0:N_BAND*N_COMP-1];
    genvar gb;
    generate
        for (gb = 0; gb < N_BAND; gb = gb + 1) begin : g_csp
            wire signed [23:0] xb [0:N_CH-1];
            wire signed [15:0] wb [0:N_CH*N_COMP-1];
            for (gc = 0; gc < N_CH; gc = gc + 1)
                assign xb[gc] = x_band[gc*N_BAND + gb];
            for (gc = 0; gc < N_CH*N_COMP; gc = gc + 1)
                assign wb[gc] = csp_w[gb*N_CH*N_COMP + gc];   // ★ 按带取
            wire signed [23:0] zb [0:N_COMP-1];
            csp_project #(.N_CH(N_CH), .N_COMP(N_COMP), .CW(16)) u_csp (
                .clk(clk), .rst_n(core_rst_n), .en(en_csp), .x(xb), .w(wb), .z(zb));
            for (gc = 0; gc < N_COMP; gc = gc + 1)
                assign z[gb*N_COMP + gc] = zb[gc];
        end
    endgenerate

    // ══════════════════════════════════════════════════════════
    //  M6：滑动窗运行和
    // ══════════════════════════════════════════════════════════
    wire signed [47:0] sz  [0:N_BAND*N_COMP-1];
    wire signed [47:0] sz2 [0:N_BAND*N_COMP-1];
    wire               win_full;

    win_var #(.N_COMP(N_BAND*N_COMP), .WIN_LEN(WIN_LEN)) u_win (
        .clk(clk), .rst_n(core_rst_n), .en(en_win), .clear(ctrl_clear_accum),
        .z(z), .sz(sz), .sz2(sz2), .win_full(win_full));

    // ══════════════════════════════════════════════════════════
    //  M7：归一化对数方差
    // ══════════════════════════════════════════════════════════
    wire               ln_done;
    wire signed [15:0] f_all [0:N_BAND*N_COMP-1];

    lognorm #(.N_COMP(N_BAND*N_COMP), .WIN_LEN(WIN_LEN), .LUT_FILE(LUT_FILE)) u_ln (
        .clk(clk), .rst_n(core_rst_n), .start(ln_go),
        .sz(sz), .sz2(sz2), .done(ln_done), .f(f_all));

    // ── MIBIF 特征选择（离线算好，这里只做选通）──
    //   来自 params/csp_svm_params.json 的 mibif_sel_idx
    localparam integer SEL0 = 1, SEL1 = 2, SEL2 = 4, SEL3 = 3;
    wire signed [15:0] f_sel [0:FEAT_DIM-1];
    generate
        for (gc = 0; gc < FEAT_DIM; gc = gc + 1) begin : g_sel
            localparam integer IDX = (gc == 0) ? SEL0 : (gc == 1) ? SEL1
                                   : (gc == 2) ? SEL2 : SEL3;
            assign f_sel[gc] = f_all[IDX];
        end
    endgenerate

    // ══════════════════════════════════════════════════════════
    //  M8：SVM 判决
    // ══════════════════════════════════════════════════════════
    svm_core #(.FEAT_DIM(FEAT_DIM), .FW(16), .QF(11), .DW(32)) u_svm (
        .clk(clk), .rst_n(core_rst_n), .en(ln_done),
        .f(f_sel), .w(svm_w), .b(svm_b),
        .d(dec_value), .label(dec_label));

    // ══════════════════════════════════════════════════════════
    //  计数与状态
    // ══════════════════════════════════════════════════════════
    reg [31:0] n_dec;
    reg        pkt_d;
    // SVM has two registered stages. Stream results are advertised only after both.
    reg [1:0] decision_valid_pipe;
    always @(posedge clk) begin
        if (!core_rst_n) decision_valid_pipe <= 0;
        else decision_valid_pipe <= {decision_valid_pipe[0], ln_done};
    end
    assign stream_done = decision_valid_pipe[1];
    always @(posedge clk) begin
        if (!rst_n || ctrl_soft_reset) begin
            n_dec  <= 32'd0;
            pkt_d  <= 1'b0;
        end else begin
            pkt_d <= sample_valid;
            if ((INPUT_MODE == 0) ? ln_done : stream_done) n_dec <= n_dec + 32'd1;
        end
    end
    assign acc_cnt  = n_dec;
    assign perf_cnt = {16'b0, seq};      // 简化：把使能流水线位置当周期数上报

    assign led_pkt   = pkt_d;
    assign led_label = dec_label;
    assign dec_valid = (INPUT_MODE == 0) ? ln_done : stream_done;

endmodule

`default_nettype wire
