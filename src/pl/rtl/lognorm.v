//=============================================================================
// lognorm.v —— M7：归一化对数方差特征
//=============================================================================
// 对应设计文档：规划/02 §4.6
//
// 数学定义：
//     var_i = ( S2_i · T − S_i² ) / T²
//     f_i   = ln( var_i / Σ_j var_j )
//
// ★★ 两条关键化简（都能省掉一级硬件）
//
//   ① 分母 T² 在比值里约掉：
//        f_i = ln( num_i / Σ num_j ),  num_i = S2_i·T − S_i²
//      → **完全不需要算 var_i，更不需要除法**
//
//   ② 商的对数 = 对数之差：
//        f_i = ln(num_i) − ln(Σ num_j)
//      → 求一次 Σ 的 log，再求 N_COMP 次各自的 log，**只需减法**
//      → 原本要做的「Newton-Raphson 求倒数 + N_COMP 次乘法」全省掉
//
//   合起来：从「1 次倒数 + N 次乘 + N 次 log」压成「N+1 次 log + N 次减」。
//
// ★ log2 的硬件实现（前导零 + 尾数 LUT + 线性插值）：
//      value = m · 2^e,  m ∈ [1,2)
//      log2(value) = e + log2(m)
//      e 由前导零计数得到；log2(m) 用 256 点表 + 线性插值
//      插值误差 ≈ (1/256)²/8 = 1.9e-6（log2 单位）→ 远小于特征 LSB(4.9e-4)
//
// ★ 为什么时间复用而不是并行
//     每采样点有 400,000 个时钟周期可用，而本模块只需 ~(N_COMP+1)×3 ≈ 21 拍。
//     并行 7 个 log 单元只会让 LUT 复制 7 份（7 个 BRAM），毫无必要。
//
// ★ 特征定义的缩放不变性（本项目另一处关键性质）
//     f_i = ln(var_i / Σ var_j) 对 z 的整体缩放完全免疫。
//     所以 CSP 权重可以任意归一化后再量化（见 zynq/ps/ea.c 的 ea_quantize_w）。
//
// 定点格式：
//     输入 S/S2 : 48 bit 整数
//     log2 中间 : Q16.14 / 32 bit
//     输出 f    : Q4.11 / 16 bit
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module lognorm #(
    parameter integer N_COMP = 6,
    parameter integer AW     = 48,        // S/S2 位宽
    parameter integer FW     = 16,        // 输出特征位宽
    parameter integer QF     = 11,        // 输出小数位（Q4.11）
    parameter integer WIN_LEN= 626,
    // ★ LUT 初值文件路径做成参数：仿真时在 sim\ 目录下，
    //   综合时 Vivado 在 run 目录下找，由 Tcl 把文件复制过去。
    parameter         LUT_FILE = "log2_lut.hex"
) (
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire                    start,       // 单拍启动
    input  wire signed [AW-1:0]    sz  [0:N_COMP-1],
    input  wire signed [AW-1:0]    sz2 [0:N_COMP-1],
    output reg                     done,
    output reg  signed [FW-1:0]    f   [0:N_COMP-1]
);

    // ── LUT：log2(1 + k/256)，k = 0..256，Q1.14 ──────────────────
    (* rom_style = "block" *)
    reg [15:0] lut [0:256];
    initial $readmemh(LUT_FILE, lut);

    // ── ln2 换算常数 ────────────────────────────────────────────
    //   f_q11 = ln(r)·2^11 = log2(r)·ln2·2^11 = log2_q14 · ln2/8
    //   ln2/8 = 0.086643397570
    //   ★ 用 S=20 而不是 S=13：
    //     第一版用 710/8192 = 0.086670，相对误差 3.06e-4，
    //     在 |f| 达到 12 时放大成 3.7e-3 的绝对误差 —— **超规格(1e-3)**。
    //     改 S=20 后相对误差 2.06e-6，|f|=12 时绝对误差 2.5e-5 ✓
    //   ★ 移位前加半 LSB 做**四舍五入**；直接用 >>> 是向 -inf 截断，
    //     会给所有负特征引入一个系统性偏置。
    localparam integer LN2_SCALE = 90841;
    localparam integer LN2_SHIFT = 20;
    localparam integer LN2_HALF  = (1 << (LN2_SHIFT - 1));

    // ── FSM ─────────────────────────────────────────────────────
    localparam S_IDLE = 4'd0,
               S_NUM  = 4'd1,     // 算 num_i = S2_i*T - S_i^2
               S_NUM2 = 4'd9,     // num 计算第二级
               S_SUM  = 4'd2,     // 求和
               S_LOG  = 4'd3,     // 逐个求 log2
               S_NORM = 4'd4,     // 归一化打拍
               S_LUT  = 4'd5,     // 查表插值打拍（级 2）
               S_LUT2 = 4'd6,     // 指数项相加打拍（级 3）
               S_OUT  = 4'd7,     // 相减乘 ln2
               S_DONE = 4'd8;     // 共 9 个状态，必须用 4 位

    reg [3:0]         st;
    reg [2:0]         idx;              // 0..N_COMP  （N_COMP 表示 Σ）
    reg [63:0]        num [0:N_COMP-1];
    // ★ num 计算的两级流水中间量（见 S_NUM / S_NUM2 的注释）
    reg signed [63:0] p_prod [0:N_COMP-1];   // sz2 · T
    reg signed [63:0] p_sq   [0:N_COMP-1];   // sz²
    reg [63:0]        sum_num;
    reg [63:0]        acc_sum;          // Declare before FSM use (XSim requires this).
    reg [31:0]        logq [0:N_COMP];  // Q16.14，最后一项存 Σ 的 log
    reg [63:0]        cur;              // 当前待求 log 的值
    reg signed [63:0] sz_ext, sz2_ext;  // num 计算用的符号扩展中间量
    reg [5:0]         e;                // 指数

    // 求一个 64 位数中最高位的位置（前导零）
    //
    // ★★ 不要写成 64 次迭代的循环！
    //   第一版：
    //       for (b = 63; b >= 0; b = b - 1)
    //           if (v[b] && msb_pos == 0) msb_pos = b;
    //   综合后是 **15 级 LUT6** 的优先编码器，实现后最差路径正是它：
    //       u_ln/cur_reg[58] → k_r_reg/ADDRARDADDR[11]
    //       Data Path 13.174 ns（logic 2.269 + route 10.905），WNS = −3.667 ns
    //
    //   改成**分级**：先选出最高的非零 16 位块（约 2 级），再在块内定位（约 4 级）。
    //   总级数约 6 级，比原来少一半以上。
    function [3:0] lz16;
        input [15:0] w;
        integer i;
        begin
            lz16 = 4'd15;
            for (i = 0; i < 16; i = i + 1)
                if (w[i]) lz16 = 4'd15 - i[3:0];
        end
    endfunction

    function [6:0] msb_pos;
        input [63:0] v;
        reg [1:0] blk;
        reg [3:0] lz;
        begin
            if      (|v[63:48]) begin blk = 2'd3; lz = lz16(v[63:48]); end
            else if (|v[47:32]) begin blk = 2'd2; lz = lz16(v[47:32]); end
            else if (|v[31:16]) begin blk = 2'd1; lz = lz16(v[31:16]); end
            else                begin blk = 2'd0; lz = lz16(v[15:0]);  end
            msb_pos = (v == 64'd0) ? 7'd0
                                   : (({5'b0, blk} << 4) + 7'd15 - {3'b0, lz});
        end
    endfunction

    // 归一化后的尾数：value << (63-e)
    wire [5:0]        e_w    = msb_pos(cur);                 // 0..63
    wire [7:0]        shift  = 8'd63 - {2'b0, e_w};
    wire [63:0]       mant   = cur << shift;                 // bit63 = 1
    wire [7:0]        k_w    = mant[62:55];                  // 8 位索引
    wire [7:0]        f_w    = mant[54:47];                  // 8 位插值
    // ══════════════════════════════════════════════════════════
    //  ★★ 两级流水线（综合时序不收敛后加的，见规划/10 §4）
    //
    //  第一版把整条 log2 写成纯组合：
    //      cur → 64 位前导零 → mant → LUT 寻址 → 插值 → (e<<14)+interp
    //  综合报告：WNS = **−22.528 ns**，**40 级逻辑**，数据路径 31.8 ns
    //  （logic 12.4 ns + route 19.5 ns），最差路径
    //      u_ln/cur_reg[60] → u_svm/acc_q23/B[15]
    //  100 MHz 下差 22.5 ns，完全不可用。
    //
    //  切两级后：
    //    级 1：cur → msb_pos → mant → k_w/f_w   （只到寄存器）
    //    级 2：查表 + 插值 + 指数移位            （只到 logval_r）
    //  代价是每次 log 求值多 2 拍。本模块一个采样点跑 7 次 → 多 14 拍，
    //  而采样间隔是 400,000 拍，**完全可以忽略**。
    //
    //  ★ 顺带修掉了原式的优先级问题：
    //      l0 + ((l1-l0)*frac) >>> 8  会被解析成 (l0 + (l1-l0)*frac) >>> 8
    //    （Verilog 里 + 的优先级高于 >>>，见规划/10 问题 10）
    // ══════════════════════════════════════════════════════════
    reg [5:0]         e_r, e_r2;
    reg [7:0]         k_r, f_r;
    reg signed [31:0] interp_r, logval_r;

    always @(posedge clk) begin
        e_r <= e_w;
        k_r <= k_w;
        f_r <= f_w;
    end

    wire signed [31:0] l0_r = {16'b0, lut[k_r]};
    // Index256 is the endpoint log2(2)=1; eight-bit addition wraps255 to0.
    wire signed [31:0] l1_r = {16'b0, lut[{1'b0, k_r} + 9'd1]};

    // ★ 再切一级：WNS = −0.377 ns 时最差路径正是这一级
    //     u_ln/k_r_reg[0] → u_ln/logval_r_reg[15]
    //     Data Path 10.075 ns，Logic Levels 10（CARRY4=4 DSP48E1=1）
    //   一拍里做了「LUT 读出 → 相减 → 乘 f_r → 右移 → 加 e<<14」。
    //   拆成：级 2 只算插值项，级 3 再把指数项加上。
    always @(posedge clk) begin
        e_r2     <= e_r;
        // ★ 括号不能省：Verilog 里 + 的优先级高于 >>>（规划/10 问题 10）
        interp_r <= l0_r + (((l1_r - l0_r) * $signed({1'b0, f_r})) >>> 8);
    end

    always @(posedge clk)
        logval_r <= ($signed({26'b0, e_r2}) <<< 14) + interp_r;

    integer n;
    always @(posedge clk) begin
        if (!rst_n) begin
            st   <= S_IDLE;
            done <= 1'b0;
            idx  <= 3'd0;
            for (n = 0; n < N_COMP; n = n + 1) begin
                num[n]    <= 64'd0;
                p_prod[n] <= 64'sd0;
                p_sq[n]   <= 64'sd0;
                f[n]      <= {FW{1'b0}};
            end
            for (n = 0; n <= N_COMP; n = n + 1) logq[n] <= 32'd0;
            sum_num <= 64'd0;
        end else begin
            done <= 1'b0;
            case (st)
                S_IDLE: if (start) begin
                    idx <= 3'd0;
                    st  <= S_NUM;
                end

                // num_i = S2_i·T − S_i²
                //
                // ★★ 必须**符号扩展** S_i，不能零扩展！
                //   S_i 是窗内 z 之和，**可正可负**（实测 6 个分量里 5 个是负的）。
                //   写成 {{16{1'b0}}, S_i} 会把 −1308 变成 ≈2^48 的大正数，
                //   平方后溢出 64 位 → num_i 变成垃圾 → 特征全错。
                //   现象：z 与 Sz 都逐位一致（前级完全正确），只有特征发散，
                //        最大差 20696（Q4.11，即 10.1，比特征本身的量级还大）。
                //   S2_i 恒非负，符号扩展与零扩展等价，但统一写成符号扩展更不易错。
                // 级 1：两个乘法
                // ★ WNS 收敛到 +0.006 ns 时最差路径就在这里：
                //     u_ln/p_0_out__4/CLK → u_ln/num_reg[1][62]/D
                //     Data Path 10.440 ns，Logic Levels 10
                //   一拍里同时做「64×64 平方 → 64×常数 → 64 位相减」。
                S_NUM: begin
                    for (n = 0; n < N_COMP; n = n + 1) begin
                        sz_ext  = {{16{sz[n][AW-1]}},  sz[n]};
                        sz2_ext = {{16{sz2[n][AW-1]}}, sz2[n]};
                        p_prod[n] <= (sz2[n] > 0) ? (sz2_ext * WIN_LEN) : 64'sd0;
                        p_sq[n]   <= (sz2[n] > 0) ? (sz_ext  * sz_ext)  : 64'sd0;
                    end
                    st <= S_NUM2;
                end

                S_NUM2: begin
                    for (n = 0; n < N_COMP; n = n + 1)
                        num[n] <= p_prod[n] - p_sq[n];
                    st <= S_SUM;
                end

                S_SUM: begin
                    // ★ 必须按 N_COMP 循环求和，不能把 6 项写死。
                    //   写死 num[0]+...+num[5] 时，N_COMP<6 的配置下 num[2..5]
                    //   是未初始化的 x，会把整个 sum 污染成 x ——
                    //   现象是 logq[N_COMP]=0、特征全错，但编译不报任何错。
                    sum_num <= acc_sum;
                    st      <= S_LOG;
                    idx     <= 3'd0;
                end

                // 逐个求 log2：idx = 0..N_COMP-1 求 num_i，idx = N_COMP 求 Σ
                // ★ 因为 log2 切成两级流水，取数要跨 3 拍：S_LOG → S_NORM → S_LUT
                S_LOG: begin
                    cur <= (idx == N_COMP) ? sum_num : num[idx];
                    st  <= S_NORM;
                end

                S_NORM: st <= S_LUT;      // 此沿锁存 e_r / k_r / f_r

                S_LUT:  st <= S_LUT2;     // 此沿锁存 interp_r

                S_LUT2: st <= S_OUT;      // 此沿锁存 logval_r

                S_OUT: begin
                    // 此时 logval_r 已是稳定的流水输出
                    if (idx == N_COMP) begin
                        logq[N_COMP] <= logval_r;
                        // 全部 log 就绪，算特征
                        //
                        // ★★ 必须显式用 64 位：Verilog 里 `*` 的结果宽度 =
                        //    两个操作数宽度的**最大值**，不是两者之和。
                        //    (logq-logval) 是 32 位、LN2_SCALE 是 32 位整数常量，
                        //    乘积会被截断成 32 位 —— 而实际量级是
                        //      |Δlog2|max ≈ 64×2^14 = 1.05e6
                        //      × 90841 ≈ 9.5e10 ≈ 2^36.5   **超出 32 位有符号**
                        //    现象：特征差恰好是 2^12（>>20 之后又放大回来）。
                        //    把常量写成 64'sd 就能强制 64 位运算。
                        for (n = 0; n < N_COMP; n = n + 1)
                            f[n] <= (((logq[n] - logval_r) * 64'sd90841)
                                     + 64'sd524288) >>> 20;
                        st <= S_DONE;
                    end else begin
                        logq[idx] <= logval_r;
                        idx <= idx + 3'd1;
                        st  <= S_LOG;
                    end
                end

                S_DONE: begin
                    done <= 1'b1;
                    st   <= S_IDLE;
                end
            endcase
        end
    end


    // Σ num_i —— 按 N_COMP 循环，不写死项数（见 S_SUM 的注释）
    integer m;
    always @(*) begin
        acc_sum = 64'd0;
        for (m = 0; m < N_COMP; m = m + 1)
            acc_sum = acc_sum + num[m];
    end

    // ── 交叉检查：e 超过 63 时视为 0（cur = 0 的退化情况）──
    // cur = 0 时 msb_pos = 0，mant = 0，log2 结果 = LUT[0] = 0
    // 对应 var = 0 的通道，特征会得到 -log2(Σ)，属合理退化。

endmodule

`default_nettype wire
