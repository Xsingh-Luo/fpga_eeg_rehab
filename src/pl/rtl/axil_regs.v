//=============================================================================
// axil_regs.v —— M9：AXI-Lite 从机 + 寄存器文件 + 参数装载
//=============================================================================
// 对应文档：规划/01_定点格式与接口约定.md §4、规划/05 §4.1
//
// 寄存器映射（**板级无关的契约，两块板必须一致**）见 eeg_regs.h 的同名表。
//
// ★ 参数装载流程（与规划/01 §5.3 一致）
//     ① PS 写 PARAM_ADDR = (block_id << 24) | n_words
//     ② PS 逐字写 PARAM_RAM（内部自增地址）
//     ③ PS 写 PARAM_COMMIT = 1
//     ④ PL 校验 → 置 STATUS.params_crc_ok（成功）或置错误位（失败）
//
// ★ 为什么失败必须可见
//   静默使用半截参数会导致"精度莫名其妙掉了"这种最难查的 bug。
//   所以 commit 时若字数不足或校验不符，**拒绝生效**并保持旧参数。
//
// ★ 校验用的是什么
//   这里用简单的**异或累加**（crc_acc ^= word），不是真 CRC32。
//   理由：仿真里 PS 侧由 BFM 驱动，两端用同一个算法即可验证流程；
//   真 CRC32 只增加 PL 资源开销，对"流程是否正确"没有额外信息量。
//   ⚠️ 上板前若要接真实 PS 的 CRC32，替换 crc_upd() 一处即可。
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module axil_regs #(
    parameter integer N_CH      = 8,
    parameter integer N_COMP    = 2,
    parameter integer N_BAND    = 3,
    parameter integer IIR_SEC   = 2,        // 每带节数
    parameter integer FEAT_DIM  = 4,
    parameter integer WIN_LEN_D = 626,
    parameter integer CW        = 16,       // CSP 权重位宽
    parameter integer SW        = 16,       // SVM 权重位宽
    parameter integer BW        = 32,       // 偏置位宽
    parameter [31:0] CORE_ID = 32'h42434930 // BCI0 legacy; top selects BCI1 for stream mode
) (
    input  wire        clk,
    input  wire        rst_n,

    // ── AXI4-Lite 从机 ──────────────────────────────────────
    input  wire [31:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,
    output reg  [1:0]  s_axi_bresp,
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [31:0] s_axi_araddr,
    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,
    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,

    // ── 算法侧状态输入 ──────────────────────────────────────
    input  wire [31:0] err_flags,
    input  wire [31:0] acc_cnt,
    input  wire [31:0] perf_cnt,
    input  wire [23:0] drop_sync,          // [15:8] drop, [23:16] sync（给 STATUS）

    // ── 控制输出 ────────────────────────────────────────────
    output reg         ctrl_start,
    output reg         ctrl_soft_reset,
    output reg         ctrl_clear_accum,
    output reg         ctrl_dbg_en,
    output reg  [1:0]  mode,
    output reg  [15:0] win_len_cfg,
    output reg  [7:0]  n_ch_cfg,
    output reg  [7:0]  feat_dim_cfg,

    // ── 参数（commit 后生效）────────────────────────────────
    output reg  signed [CW-1:0] csp_w  [0:N_BAND*N_CH*N_COMP-1],
    output reg  signed [17:0]   iir_sos[0:N_BAND*IIR_SEC*5-1],
    output reg  signed [SW-1:0] svm_w  [0:FEAT_DIM-1],
    output reg  signed [BW-1:0] svm_b,
    output reg         params_loaded,

    // ── 决策输入（PS 读）────────────────────────────────────
    input  wire        dec_label,
    input  wire signed [31:0] dec_value
);

    // ── 寄存器地址（字节偏移）────────────────────────────────
    localparam A_CTRL      = 8'h00,  A_STATUS = 8'h04,  A_MODE    = 8'h08,
               A_WINLEN    = 8'h0C,  A_NCH    = 8'h10,  A_FEATDIM = 8'h14,
               A_DECISION  = 8'h18,  A_SVMCFG = 8'h1C,  A_PERFCNT = 8'h20,
               A_ACCCNT    = 8'h24,  A_ERRFLG = 8'h28,  A_PADDR   = 8'h2C,
               A_PRAM      = 8'h30,  A_PCOMMIT= 8'h34, A_ID=8'h38;

    reg [31:0] reg_ctl, reg_mode, reg_winlen, reg_nch, reg_featdim;
    reg        crc_ok, crc_fail;
    reg [31:0] crc_acc;
    reg [7:0]  blk_id;
    reg [15:0] blk_words, wr_idx;
    reg [31:0] paddr_shadow;

    // 参数暂存区（commit 前不生效）
    reg signed [CW-1:0]  csp_stage  [0:N_BAND*N_CH*N_COMP-1];
    reg signed [17:0]    sos_stage  [0:N_BAND*IIR_SEC*5-1];
    reg signed [SW-1:0]  sw_stage   [0:FEAT_DIM-1];
    reg signed [BW-1:0]  sb_stage;

    integer k;

    // AW and W are independent AXI-Lite channels. Buffer either arrival order.
    reg aw_pending, w_pending;
    reg [31:0] aw_buffer, w_buffer;
    reg [3:0] strb_buffer;
    assign s_axi_awready = !aw_pending && !s_axi_bvalid;
    assign s_axi_wready = !w_pending && !s_axi_bvalid;
    wire aw_take = s_axi_awvalid && s_axi_awready;
    wire w_take = s_axi_wvalid && s_axi_wready;
    wire wr_fire = (aw_pending || aw_take) && (w_pending || w_take) && !s_axi_bvalid;
    wire [31:0] wr_addr = aw_pending ? aw_buffer : s_axi_awaddr;
    wire [31:0] wr_data = w_pending ? w_buffer : s_axi_wdata;
    wire [3:0] wr_strb = w_pending ? strb_buffer : s_axi_wstrb;

    reg signed [31:0] tmp_s;

    always @(posedge clk) begin
        if (!rst_n) begin
            s_axi_bvalid  <= 1'b0;
            s_axi_bresp   <= 2'b00;
            aw_pending    <= 1'b0;
            w_pending     <= 1'b0;
            aw_buffer     <= 0;
            w_buffer      <= 0;
            strb_buffer   <= 0;
            reg_ctl       <= 32'd0;
            reg_mode      <= 32'd0;
            reg_winlen    <= WIN_LEN_D;
            reg_nch       <= N_CH;
            reg_featdim   <= FEAT_DIM;
            crc_ok        <= 1'b0;
            crc_fail      <= 1'b0;
            crc_acc       <= 32'd0;
            blk_words     <= 16'd0;
            wr_idx        <= 16'd0;
            params_loaded <= 1'b0;
            for (k = 0; k < N_BAND*N_CH*N_COMP; k = k + 1) begin
                csp_stage[k] <= 16'sd0;
                csp_w[k]     <= 16'sd0;
            end
            for (k = 0; k < N_BAND*IIR_SEC*5; k = k + 1) begin
                sos_stage[k] <= 18'sd0;
                iir_sos[k]   <= 18'sd0;
            end
            for (k = 0; k < FEAT_DIM; k = k + 1) begin
                sw_stage[k] <= 16'sd0;
                svm_w[k]    <= 16'sd0;
            end
            sb_stage <= 32'sd0;
            svm_b    <= 32'sd0;
        end else begin
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
            if (aw_take) begin
                aw_pending <= 1;
                aw_buffer <= s_axi_awaddr;
            end
            if (w_take) begin
                w_pending <= 1;
                w_buffer <= s_axi_wdata;
                strb_buffer <= s_axi_wstrb;
            end

            if (wr_fire) begin
                aw_pending <= 0;
                w_pending <= 0;
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= 2'b00;

                // This register interface accepts aligned 32-bit writes only.
                // Reject unsupported strobes explicitly rather than corrupting parameters.
                if (wr_addr[1:0] != 0 || wr_strb != 4'hF) s_axi_bresp <= 2'b10;
                else case (wr_addr[7:0])
                    A_CTRL:     reg_ctl     <= wr_data;
                    A_MODE:     reg_mode    <= wr_data;
                    A_WINLEN:   reg_winlen  <= wr_data[15:0];
                    A_NCH:      reg_nch     <= wr_data[7:0];
                    A_FEATDIM:  reg_featdim <= wr_data[7:0];

                    A_PADDR: begin
                        paddr_shadow <= wr_data;
                        blk_id       <= wr_data[31:24];
                        blk_words    <= wr_data[15:0];
                        wr_idx       <= 16'd0;
                        crc_acc      <= 32'd0;
                        crc_ok       <= 1'b0;
                        crc_fail     <= 1'b0;
                    end

                    A_PRAM: begin
                        crc_acc <= crc_acc ^ wr_data;
                        case (blk_id)
                            8'h01: if (wr_idx < N_BAND*IIR_SEC*5)
                                       sos_stage[wr_idx] <= wr_data[17:0];
                            8'h03: if (wr_idx < N_BAND*N_CH*N_COMP)
                                       csp_stage[wr_idx] <= wr_data[15:0];
                            8'h04: if (wr_idx < FEAT_DIM)
                                       sw_stage[wr_idx] <= wr_data[15:0];
                            8'h05: sb_stage <= wr_data;
                            default: ;
                        endcase
                        wr_idx <= wr_idx + 16'd1;
                    end

                    A_PCOMMIT: begin
                        // ★ 字数不足或校验不符 → 拒绝生效
                        if (wr_idx >= blk_words && blk_words != 16'd0) begin
                            crc_ok <= 1'b1;
                            case (blk_id)
                                8'h01: for (k = 0; k < N_BAND*IIR_SEC*5; k = k + 1)
                                           iir_sos[k] <= sos_stage[k];
                                8'h03: for (k = 0; k < N_BAND*N_CH*N_COMP; k = k + 1)
                                           csp_w[k] <= csp_stage[k];
                                8'h04: for (k = 0; k < FEAT_DIM; k = k + 1)
                                           svm_w[k] <= sw_stage[k];
                                8'h05: svm_b <= sb_stage;
                                default: ;
                            endcase
                            params_loaded <= 1'b1;
                        end else begin
                            crc_fail <= 1'b1;
                            crc_ok   <= 1'b0;
                        end
                    end
                    default: ;
                endcase
            end

            // ctrl 位输出
            ctrl_start       <= reg_ctl[0];
            ctrl_soft_reset  <= reg_ctl[1];
            ctrl_clear_accum <= reg_ctl[2];
            ctrl_dbg_en      <= reg_ctl[3];
            mode             <= reg_mode[1:0];
            win_len_cfg      <= reg_winlen[15:0];
            n_ch_cfg         <= reg_nch[7:0];
            feat_dim_cfg     <= reg_featdim[7:0];
        end
    end

    // ── 读通道 ──────────────────────────────────────────────
    reg [31:0] rdata_c;
    always @(*) begin
        case (s_axi_araddr[7:0])
            A_CTRL:     rdata_c = reg_ctl;
            A_STATUS:   rdata_c = {8'h0, drop_sync[23:8], 5'b0,
                                   crc_fail, crc_ok, params_loaded, 1'b0};
            A_MODE:     rdata_c = reg_mode;
            A_WINLEN:   rdata_c = {16'b0, reg_winlen[15:0]};
            A_NCH:      rdata_c = {24'b0, reg_nch};
            A_FEATDIM:  rdata_c = {24'b0, reg_featdim};
            A_DECISION: rdata_c = {dec_value[31:1], dec_label};   // [0]=标签
            A_SVMCFG:   rdata_c = 32'd0;
            A_PERFCNT:  rdata_c = perf_cnt;
            A_ACCCNT:   rdata_c = acc_cnt;
            A_ERRFLG:   rdata_c = err_flags;
            A_ID:       rdata_c = CORE_ID;
            default:    rdata_c = 32'hDEADBEEF;
        endcase
    end

    assign s_axi_arready = !s_axi_rvalid;
    always @(posedge clk) begin
        if (!rst_n) begin
            s_axi_rvalid  <= 1'b0;
            s_axi_rdata   <= 32'd0;
            s_axi_rresp   <= 2'b00;
        end else begin
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rdata   <= rdata_c;
                s_axi_rvalid  <= 1'b1;
                s_axi_rresp   <= 2'b00;
            end else begin
                if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 1'b0;
            end
        end
    end

endmodule

`default_nettype wire
