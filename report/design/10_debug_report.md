# 10 · 调试报告

> 配套：[09 PL/PS 设计文档](09_pl_ps_design.md)
> 环境：iverilog 12.0（功能仿真）· Vivado 2025.1（综合）· aarch64-linux-gnu-gcc 13.3.0（PS 交叉编译）
> 版本 v1.0 ｜ **本报告取代此前所有调试/验证记录**

---

## 1. 仿真环境

| 工具 | 版本 / 路径 | 用途 |
|---|---|---|
| iverilog + vvp | 12.0 / `D:\iverilog\bin` | RTL 功能仿真（主力，秒级迭代） |
| ModelSim | `D:\Modelsim\win64` | 备用（波形调试） |
| Vivado | 2025.1 / `D:\Xilinx\2025.1\Vivado` | 综合与实现 |
| aarch64-linux-gnu-gcc | 13.3.0 / `D:\Xilinx\2025.1\gnu\aarch64` | PS 侧交叉编译 |
| Python | `.venv`（numpy/scipy） | 生成逐位定点参考模型 |

**为什么主力用 iverilog 而不是 Vivado 仿真**：Vivado `xsim` 启动要 30 s 以上，
iverilog 编译+仿真不到 2 s。在"改一行、跑一次"的迭代节奏下，
**这个差距直接决定能不能坚持每改必测**。

---

## 2. 发现并解决的问题

### 问题 1 ★ 超时阈值单位搞错（位周期 vs 字节周期）

**现象**：`tb_pkt_parser` 全部用例失败，`pkt_parser` 一个样本都不输出。

**定位**：加临时追踪 `tb_debug.v` 打印每个收到的字节与解析器内部状态：

```
[84715000] byte#1 = 0x41   parser.state=0 idx=0     ← 正确锁上 0x41
[102095000] !! sync_err 0 -> 1                       ← 17.36 µs 后超时
[171515000] byte#2 = 0x07  parser.state=0 idx=1     ← 但下一字节 86.8 µs 才到
```

**根因**：超时阈值算成了

```verilog
localparam BYTE_TICKS = CLK_FREQ / BAUD;        // 868 —— 这是 1 个「位」的周期数！
localparam TIMEOUT    = BYTE_TICKS * 2;         // 1736 周期 = 17.4 µs
```

`CLK_FREQ/BAUD = 868` 是**位**周期。而 1 个**字节** = 起始位 + 8 数据位 + 停止位 = **10 位**
= 8680 周期 = 86.8 µs。阈值比实际字节间隔小 5 倍，所以每收一个字节就误判断流。

**修法**：

```verilog
localparam BIT_TICKS  = CLK_FREQ / BAUD;              // 868
localparam BYTE_TICKS = 10 * BIT_TICKS;               // 8680  ← 补上 ×10
localparam TIMEOUT    = BYTE_TICKS * TOUT_BYTE;       // 17360 ≈ 174 µs
```

**教训**：注释里明明写着"17360 周期 ≈ 174 µs"，代码却写成了 1736。
**注释和代码不一致时，仿真一定会站在错误的那一边。**

---

### 问题 2 ★★ 超时计数器未在进入接收态时清零 → 此后永远收不到数据

**现象**：修好问题 1 后，`cnt_pkt` 停在 4 再也不增长，而 `sync_err` 一路涨到 6。
即"第一个超时发生后，解析器再也锁不上任何包"。

**定位**：诊断打印显示第一个超时正常触发，但之后每个包都瞬间超时。

**根因**：`tout` 只在 `state == S_RECV` 分支里被 `byte_valid` 清零。

```verilog
if (state == S_RECV) begin
    if (byte_valid) tout <= 0;
    else if (tout == TIMEOUT) begin state <= S_IDLE; ... end
    else tout <= tout + 1;
end
```

超时后 `tout` 停在 `TIMEOUT` 且**在 IDLE 期间不再变化**。下次收到 `0x41` 时：

| 拍 | `state` | `tout` | 发生了什么 |
|---|---|---|---|
| k | IDLE | = TIMEOUT | 收到 `0x41`，非阻塞赋值 `state <= S_RECV` 下拍才生效 |
| k+1 | **S_RECV** | **仍是 TIMEOUT**（第 k 拍 `state` 还是 IDLE，清零分支没进） | **立刻判超时，回 IDLE** |

→ **每一个包都在第 2 拍被判超时，永远收不满 33 字节。**

**修法**：在**进入 S_RECV 的那一刻**清零，并在超时分支也复位：

```verilog
S_IDLE: if (byte_in == BOP) begin
            idx  <= 6'd1;
            tout <= 0;          // ★ 关键
            state <= S_RECV;
        end
...
else if (tout == TIMEOUT) begin
    state <= S_IDLE;
    tout  <= 0;                 // ★ 双保险
    cnt_sync_err <= cnt_sync_err + 1;
end
```

**教训**：**非阻塞赋值有一拍延迟**，`state <= S_RECV` 与"在 S_RECV 里做的事"不在同一拍。
凡是"进入某状态时要初始化什么"的逻辑，必须写在**状态跳转的那一拍**，
不能依赖新状态里的清零分支。

这个 bug 的隐蔽性在于：**第一个包永远是对的**（`tout` 初值为 0），
只有发生过一次超时之后才暴露——如果测试只测正常包，根本发现不了。

---

### 问题 3 ★ 级联 biquad 的使能错位 → 输出整体延迟一个采样

**现象**：`tb_biquad_bank` 报 451/808 样本不一致，且差值模式很规律：
`got[n] == exp[n-1]`（整体后移一拍）。

**根因**：`biquad_cascade` 里两节共用同一个 `en`：

```verilog
biquad u_s0 (... .en(en),  .x(x),  .y(y0));
biquad u_s1 (... .en(en),  .x(y0), .y(y));   // ← y0 在同一拍才更新
```

第 k 拍 `u_s1` 读到的 `y0` 是**第 k 拍时钟沿才更新**的值，即第 k−1 拍的旧数据。
两节之间错位一拍，整个级联输出后移一个采样。

**修法**：给第 2 节的使能打一拍：

```verilog
reg en_d;
always @(posedge clk) en_d <= rst_n ? en : 1'b0;
biquad u_s1 (... .en(en_d), .x(y0), .y(y));
```

级联总延迟变成 2 个 `en` 节拍。因为采样率只有 250 Hz（4 ms 一个点），
**这 2 拍延迟完全无所谓**；但错位会让滤波器响应完全错误。

**修完后**：808 个样本**逐位一致** ✓

**教训**：级联的流水级之间，使能必须逐级延迟。
**不能用同一个 en 驱动多级流水**，除非各级之间是纯组合路径。

---

### 问题 4 ★★ EA 折叠后 CSP 权重溢出（Q3.14/18bit 不够）

**现象**：`verify_ea_math.py` 报告——3 通道没问题，**8 通道下 5~10 个权重饱和**。

**根因**：欧氏对齐的白化矩阵在 $\bar R$ 有小特征值时会放大权重。

| 通道数 | $\lvert \tilde W \rvert_{\max}$ | $\lvert W_{\text{eff}} \rvert_{\max}$ | Q3.14/18bit（±8.0） |
|---|---|---|---|
| 3（BCI IV 2b） | < 2.0 | 3.81 | ✓ 余量 52% |
| 8（自采 Cyton） | ≈ 2.2 | **19 ~ 20** | **✗ 饱和** |

**危险之处**：这是**静默**的——权重被截到 ±8.0，精度掉几个点，
但代码不报任何错，而且 3 通道验证时完全正常，很容易到 8 通道才炸。

**修法**：利用**特征定义的缩放不变性**。

$$f_i = \log\frac{\text{var}(z_i)}{\sum_j \text{var}(z_j)}, \qquad
W \to \alpha W \Rightarrow z \to \alpha z \Rightarrow \text{var} \to \alpha^2 \text{var}
\Rightarrow \frac{\text{var}_i}{\sum_j \text{var}_j} \text{ 不变}$$

所以可以**免费**把 $W_{\text{eff}}$ 归一化到 $\max = 0.875$ 再量化：

| 方案 | 饱和数（8 通道） | 特征误差 | 系数位宽 |
|---|---|---|---|
| A 直接 Q3.14/18bit | **5 ~ 10** ✗ | — | 18 |
| **B 归一化 + Q1.14/16bit** | **0** ✓ | **2.5e-4** | **16** |

**方案 B 不但消除了溢出，还把系数位宽从 18 降到 16** ——
18×24 和 16×24 都装得进 DSP48E1 的 25×18，但 16 bit 的路由和布线更宽松。

**规格对照**：特征相对误差要求 < 1e-3（[01 号文档](01_fixed_point_interface.md) §3.4），
实测 2.5e-4 ✓

**教训**：**"验证通过"要问清是在什么条件下通过的。**
3 通道的数据集上这个问题根本不会出现——是换到 8 通道的推演才暴露的。

---

### 问题 5（测试台自身的坑）多驱动导致结果随机

**现象**：`tb_pkt_parser` 在 T2b 之后的用例随机失败，同一份代码两次运行结果不同。

**根因**：测试台里 `cap_valid` 等信号**同时被 `always` 块和 `initial` 块赋值**：

```verilog
always @(posedge clk) if (sample_valid) cap_valid <= 1'b1;   // 驱动 1
initial begin ... cap_valid = 1'b0; ... end                   // 驱动 2 ← 多驱动！
```

**修法**：`cap_*` 只由 `always` 驱动，判据改用**样本计数器增量**：

```verilog
integer cap_count = 0;
always @(posedge clk) if (sample_valid) cap_count = cap_count + 1;
// 用例里：c0 = cap_count; ...; check(cap_count == c0 + 1, "...");
```

**教训**：测试台也会有多驱动问题，而且症状（结果随机）比 RTL 的 bug 更难查。
**凡是被 `always` 驱动的信号，`initial` 里就不要写。**

### 问题 6（联合仿真）测试台的时间尺度算错

**现象**：联合仿真跑了一小时没结果，vvp 烧掉 1183 秒 CPU，dump 文件仍是 0 字节。

**根因**：为了缩短仿真时间把时钟参数等比缩小（`CLK_FREQ=1000, BAUD=100` → DIV=10），
但位周期写成了：

```verilog
localparam real BIT_NS = 1e9 * DIV / CLK_FREQ;   // = 1e9*10/1000 = 1e7 ns = 10 ms ✗
```

`1e9/f` 求得的是**周期**，而这里的时钟是固定 100 MHz（周期 10 ns），
`CLK_FREQ` 只是用来算 DIV 的名义值。结果每个位要 100 万个时钟周期，
24552 字节 × 10 位 × 100 万 = **2.45×10¹¹ 周期**。

**修法**：

```verilog
localparam real CLK_NS = 10.0;          // 时钟周期固定 10 ns
localparam real BIT_NS = DIV * CLK_NS;  // = 10 × 10 = 100 ns ✓
```

**教训**：**"等比缩小"只能缩一个量，不能把时钟频率和位周期一起缩。**
时钟是仿真的时间基准，缩了它所有绝对时间都失去参照。
正确做法是**固定时钟周期，只缩"每位的时钟数"**。

这也是同一类错误的第三次出现（问题 1 是位/字节周期混淆，这次是频率/周期混淆）——
**凡是有"周期"两个字的参数，都要写清单位并在注释里给出算式。**

---

### 问题 7（联合仿真）biquad 输出抓取时机差一拍

**现象**：biquad 输出与逐位模型 732/744 不一致，最大差 92。

**定位**：差值呈"整体错位"特征，而非随机噪声 → 是采样时机问题。

**根因**：级联的输出延迟是 **3 拍**，测试台只等了 2 拍：

| 拍 | `sample_valid` | `bq_en` | 级联内部 `en_d` | sec0 输出 | sec1 输出 | 抓取信号 |
|---|---|---|---|---|---|---|
| k | **1** | 0 | 0 | — | — | |
| k+1 | 0 | **1** | 0 | x[k] | — | `sv_d1` |
| k+2 | 0 | 0 | **1** | — | y0 | `sv_d2` ← 用错了 |
| k+3 | 0 | 0 | 0 | — | **y** | **`sv_d3`** ← 应该用这个 |

`sv_d3` 声明了却没使用。

**修法**：抓取条件由 `sv_d2` 改为 `sv_d3` → **逐位一致**。

**教训**：**级联模块的输出延迟要靠"数使能拍数"推出来，不能凭感觉。**
更稳妥的写法是在 RTL 里把 valid 一起流水（valid 跟着数据走），
而不是让测试台去猜延迟——这也是后续给 `lognorm`/`svm_core` 写 TB 时要注意的。

---

## 2.5 ★ 联合仿真（与 `sim/host_tools/cyton_sim.py` 打通）

**这是"四级对拍"的第 4 级**，也是最有说服力的一级：
**让 RTL 和 PC 端解析器吃同一份字节流**，证明两边对协议的理解完全一致。

### 激励来源

`sim/host_tools/sample_stream.bin` —— 744 包、24552 字节，
由 `sim/host_tools/cyton_sim.py` 从 BCI IV 2b 真实数据生成。
版本管理里把它列为「**不可再生的验证资产**」，正是为这种对拍准备的。

### 链路

```
sim/host_tools/sample_stream.bin  (744 包真实 Cyton 字节流)
        │
        ├──► cyton_parse.py ──────────────► 参考样本 (744×3)
        │
        └──► stim_cyton.hex ──► tb_cosim.v
                                  │
                    uart_rx ──► pkt_parser ──► biquad_cascade(带0)
                                  │                    │
                              dump_raw.txt         dump_bq.txt
                                  │                    │
                                  └──── cosim_cyton.py ─┘
                                         逐样本比对
```

### 结果

```
[cosim] 完成: cnt_pkt=744 footer_err=0 sync_err=0 drop=0 frame_err=0
[对拍] 原始样本（3 通道 × 744 包）        最大差 = 0   不一致 = 0
[对拍] biquad 带0 输出（744 样本）        最大差 = 0   不一致 = 0
```

**744 包零错误，两个对拍点全部逐样本一致。**

> **注意口径**：联合仿真为了速度用了**等比缩小**的位周期（DIV=10 而非 868），
> 逻辑完全一致，但**位宽相关的验证**由 `tb_pkt_parser.v`（DIV=868）覆盖。
> 两者合起来才完整：一个管"数据对不对"，一个管"位宽够不够"。

### 复现

```powershell
cd zynq\pl\sim
& H:\FPGA2026QS\.venv\Scripts\python.exe cosim_cyton.py
```

---


### 问题 8（数据通路）流水线使能未逐级延迟

**现象**：`tb_datapath` 里 L1（`csp_project`）逐位一致，但 L2（`win_var`）的 `Sz` 差 161。

**根因**：`csp_project` 在 `en` 沿寄存 `z`，而 `win_var` 用**同一个** `en`——
同一时钟沿 `win_var` 读到的是**上一个**采样点的 `z`。整个窗和错位一个样本。

这已是**同一类错误的第三次**（另两次见问题 3 的 biquad 级联、问题 7 的抓取时机）。

**修法**：使能逐级延迟。

**结论**：**流水线的每一级都要有自己的、按上游延迟错开的使能。**
顶层 `eeg_csp_svm_top` 必须显式维护一条使能流水线。

---

### 问题 9（数据通路）求和项数写死

**现象**：L3 `lognorm` 全错，探针显示 `sum_num = x`（未知态）。

**根因**：求和写死了 6 项，而本测试 `N_COMP=2`：

```verilog
sum_num <= num[0] + num[1] + num[2] + num[3] + num[4] + num[5];  // ✗
```

`num[2..5]` 未初始化 → 整个和变成 `x` → 全部特征错。**编译不报任何错。**

**修法**：按 `N_COMP` 循环累加。

**教训**：**参数化模块里任何"写死的下标"都是定时炸弹**——
只在参数恰好等于那个值时才正确。

---

### 问题 10 ★★ 运算符优先级：`+` 高于 `>>>`

**现象**：`log2` 结果系统性偏小，`interp` 本该 8159 却是 66。

**根因**：

```verilog
wire [31:0] interp = l0 + (((l1 - l0) * frac)) >>> 8;    // ✗
// 实际被解析成：
//   interp = ( l0 + (l1-l0)*frac ) >>> 8      ← 整个和都被右移了
```

**验算完全吻合**：idx=0 时 $l_0{=}8124$、$(l_1{-}l_0){\cdot}frac{=}8970$，
$(8124{+}8970)\gg 8 = 66$ —— 与观测到的错误值**一字不差**；
idx=2 时 $(1433{+}13659)\gg 8 = 58$，同样吻合。

**修法**：补括号。

**教训**：**Verilog 的运算符优先级和 C 不同，位移低于加减。**
凡是有位移的表达式，括号全部加上，不要依赖记忆。

---

### 问题 11 ★★ Verilog 的 `*` 结果宽度是 max(操作数宽度)，不是和

**现象**：`log2` 各级都对了、`logq` 也对了，但特征差**恰好是 4096 = 2¹²**。
差值如此"整"说明是溢出截断，不是精度问题。

**根因**：

```verilog
f[n] <= (((logq[n] - logval) * LN2_SCALE) + LN2_HALF) >>> LN2_SHIFT;   // ✗
```

`(logq-logval)` 是 32 位、`LN2_SCALE` 是 32 位常量 → **`*` 结果被截成 32 位**
（Verilog 取两个操作数宽度的**较大者**，不是和）。

实际量级：$|\Delta\log_2|_{\max}\approx 64\times2^{14}=1.05\times10^6$，
$\times 90841 \approx 9.5\times10^{10}\approx2^{36.5}$ —— **超出 32 位有符号**。

**修法**：把常量显式写成 64 位，强制 64 位运算：

```verilog
f[n] <= (((logq[n] - logval) * 64'sd90841) + 64'sd524288) >>> 20;   // ✓
```

**教训**：**Verilog 的位宽推导规则和 C 完全不同** —— C 里 `int*int` 至少是 `int`，
Verilog 里结果宽度**不会自动扩展**。凡可能超 $2^{31}$ 的乘法，必须显式给操作数定宽。

这个 bug 尤其危险：`log2` 全对、`logq` 全对，只有最后一步的系数换算错了，
**如果只看最终准确率，很可能被当成量化误差忽略掉**。

---

## 2.7 ★ 数据通路逐级对拍

把 M5→M6→M7→M8 串起来，每级都 dump 出来与 Python 逐位参考模型比对。

**为什么必须逐级 dump**：最终标签只有 1 bit，中间任何一级错了它都可能"碰巧对"。
本轮正是靠逐级 dump 才把问题 8/9/10/11 逐个定位到具体模块。

```
[dp] 完成: 200 样本, win_full=1

L1 csp_project   z:  200 样本 × 2 分量，最大差 0  ✓
L2 win_var       Sz: 200 样本，最大差 0  ✓
                 S2: 最大差 0  ✓
L3 lognorm       f:  2 维，最大差 0  ✓
L4 svm_core      d:  RTL=-3450851  参考=-3450851  ✓
                 标签: RTL=0  参考=0  ✓

定点 vs 浮点特征误差 = 1.895e-04（规格 < 1e-3）  ✓
```

**四级全部逐位一致。**

复现：`python verify_datapath.py`

---

## 2.9 ★★ 全流程仿真（PL + PS，含 AXI）

**这是目前最完整的一级**：真实 Cyton 字节流进 PL，PS 通过 AXI 下发参数并读回决策。

```
sim/host_tools/sample_stream.bin (744 包)
        │
        ▼
   tb_fullflow.v ── uart_rx → pkt_parser → dc_block → biquad_bank
        │                                      → csp_project → win_var
        │                                      → lognorm → svm_core
        │                                            │
   AXI 主设备 BFM（模拟 PS）                          ▼
   ① 下发 SOS/CSP_W/SVM_W/SVM_B ──────────► axil_regs
   ② 轮询 DECISION 读回        ◄──────────
        │
        ▼
   verify_fullflow.py ── 逐位参考流水线对拍
```

### 结果

```
[ff] PS: STATUS=0x00000006  params_loaded=1 crc_ok=1     ← AXI 参数下发 ✓
[ff] 完成: cnt_pkt=744  决策数=744
[ff] 错误: footer=0 sync=0 drop=0 frame=0                ← 744 包零错误

中间量逐级比对
  z (CSP)    最大差 = 0  ✓
  Sz (窗和)  最大差 = 0  ✓
  f (特征)   最大差 = 0  ✓
全流程对拍
  标签不一致    = 0  ✓
  决策值不一致  = 0  ✓   （118 个决策逐位一致）
```

**从字节流到决策标签，整条链逐位一致。**

### 本轮抓到的 5 个问题

| # | 问题 | 现象 | 为什么危险 |
|---|---|---|---|
| 12 | `axil_regs` 的 CSP 权重只有一组 | 3 个带共用一组权重 | 每带必须有各自的 W |
| 13 | JSON 是 Q3.14/18bit，契约是 Q1.14/16bit | `z` 最大差 212 | **静默截断**，18 位塞进 16 位 |
| 14 | ★ `lognorm` 对 `Sz` **零扩展**而非符号扩展 | 特征差 20696 | 见下 |
| 15 | 漏了特征标准化 | 决策值全错 | 已折进 SVM 权重 |
| 16 | 决策 dump 的 off-by-one | 决策值差 45796 | 测试台问题 |

### 问题 14 详解 —— 本轮最有价值的发现

`Sz` 是窗内 $z$ 之和，**可正可负**。RTL 里写的是：

```verilog
{{16{1'b0}}, sz[n][AW-1:0]} * {{16{1'b0}}, sz[n][AW-1:0]}   // ✗ 零扩展
```

零扩展把 $-1308$ 变成 $\approx 2^{48}$ 的大正数，平方后溢出 64 位。

**实测数据**：$Sz = [335, \mathbf{-1308}, -5, \mathbf{-667}, -161, \mathbf{-485}]$ —— 6 个里 5 个是负的。

**为什么特别难查**：`z` 和 `Sz` 都**逐位一致**（前级完全正确），
只有特征发散，而且最大差 20696（Q4.11 即 10.1）**比特征本身的量级还大**。
如果不做逐级 dump，只会看到"最终准确率很低"，根本定位不到。

**修法**：符号扩展

```verilog
sz_ext  = {{16{sz[n][AW-1]}},  sz[n]};     // ✓ 符号扩展
sz2_ext = {{16{sz2[n][AW-1]}}, sz2[n]};    // S2 恒非负，但统一写法更不易错
num[n] <= sz2_ext * WIN_LEN - sz_ext * sz_ext;
```

**教训**：**Verilog 里 `{N{1'b0}}` 连接和 `$signed()` 的语义完全不同。**
凡是对有符号数做扩展，必须用符号位填充 `{N{x[MSB]}}`。
这和问题 11 是同一类"位宽/符号"陷阱。

### 问题 15：特征标准化的折叠（第三次用同一个原理）

训练好的 SVM 吃的是**标准化后**的特征：

$$d = \sum_i w_i\frac{f_i-\mu_i}{sd_i} + b$$

展开后：

$$d = \sum_i \underbrace{\frac{w_i}{sd_i}}_{w'_i} f_i + \underbrace{\Big(b-\sum_i\frac{w_i\mu_i}{sd_i}\Big)}_{b'}$$

**PL 侧零改动。**（前两次：EA 白化折进 CSP 投影、线性 SVM 折叠成 $w\cdot f+b$。）

⚠️ 标度不能算错：$w$、$sd$ 都是 Q4.11，所以

$$w'_{q11} = \mathrm{round}\!\left(\frac{w_{raw}\times 2048}{sd_{q11}}\right)$$

漏掉 $\times 2048$ 会让量化后只剩 $\pm 7$，精度全丢（踩过一次）。
折叠后 $|w'|_{\max}=15152 < 32767$，**余量 54%**。

### 复现

```powershell
cd zynq\pl\sim
& H:\FPGA2026QS\.venv\Scripts\python.exe verify_fullflow.py
```

---

## 3. 时序优化

### 3.1 已做的结构性优化

| 模块 | 关键路径 | 优化手段 | 效果 |
|---|---|---|---|
| `biquad` | 反馈链 `s1 → acc → y → ×a1 → s1` | **用转置直接 II 型**，反馈链只 1 次乘加 | 直接 I 型要两级累加，100 MHz 下会违例 |
| `biquad` | 18×24 乘法 | 系数 Q3.14/18bit，数据 24bit | 24×18 装进 DSP48E1 的 25×18，**1 个 DSP/乘法** |
| `csp_project` | 48 位加法树 | 见 §3.2 待做项 | — |
| `win_var` | BRAM 读出 → 平方 → 48 位加减 | 按"读地址 1 拍 → 读出 1 拍 → 运算 1 拍"流水 | 切断长组合路径 |

### 3.2 待做的时序优化（上板前必查）

| 优先级 | 项 | 做法 |
|---|---|---|
| **P0** | CSP 加法树 | `csp_project` 的 $N$ 项累加是全组合的。N=8 时是 8 级串行加法，**综合后若报 setup 违例，在加法树中间插 1~2 级寄存器**（流水线化，代价是 1~2 拍延迟，采样率 250 Hz 下完全无所谓） |
| **P0** | biquad 系数寄存器 | 系数从 AXI-Lite 直接进乘法器会拉长路径，**中间加一级寄存器** |
| P1 | `win_var` 的 $z^2$ | 48 位加减器可拆成 2 级 |
| P2 | 全局 | `set_property MAX_FANOUT` 控制复位/使能网络扇出 |

> **本项目对时序的容忍度很高**：采样率只有 250 Hz，4 ms 里有 400,000 个时钟周期。
> 所以**优先用"多打几拍"换时序收敛**，而不是压缩逻辑级数。
> 唯一要守住的是 UART 接收路径——它的 868 周期计数是**实时约束**，不能用流水线解决。

### 3.3 资源估算（待 Vivado 综合确认）

| 模块 | LUT | DSP | BRAM36 |
|---|---|---|---|
| `uart_rx` + `pkt_parser` | ~200 | 0 | 0 |
| `biquad_bank`（3 带 × 2 节 × 8 通道） | ~1.8K | 2–4（时分复用） | 0 |
| `csp_project`（3 带 × 8 通道 × 2 分量） | ~0.7K | 48（全并行） | 0 |
| `win_var`（6 分量 × 626 点） | ~0.9K | 0 | 3–4 |
| `lognorm` + `svm_core` | ~0.9K | 6 | 1 |
| `axil_regs` | ~1.2K | 0 | 0 |
| **合计** | **~6K（11%）** | **~60（27%）** | **~5（4%）** |

> **DSP 是全并行 CSP 的代价**（48 个）。若资源紧张，把 `csp_project` 改成时分复用
> 可降到 4 个 DSP，代价是控制状态机复杂度（见 [09 号文档](09_pl_ps_design.md) §2 M5）。

---

## 4. ★★ Vivado 综合与实现实测（2025.1，xc7z020clg484-2）

### 4.1 资源与器件占用

| 项 | 实测 | 占器件 | 我原先的估算 | 结论 |
|---|---|---|---|---|
| Slice LUTs | **8614** | 16.19% | ~6K | 接近，略高 |
| LUT as Logic | 6886 | 12.94% | — | |
| LUT as Memory | 1728 | 9.93% | — | **`win_var` 的 ring 落到分布式 RAM** |
| Slice Registers | 3112 | 2.92% | — | |
| **Block RAM** | **0.5 tile** | **0.36%** | 3~4 tile | ✗ **几乎没用上 BRAM** |
| **DSP48E1** | **194** | **69%** | ~60 | ✗ **严重低估 3 倍** |
| Bonded IOB | 101 | 50.5% | — | |
| **WNS (setup)** | **−3.169 ns** | | — | ✗ **未收敛** |
| WHS (hold) | +0.040 ns | | — | ✓ |

### 4.2 时序优化的九轮迭代（WNS 从 −22.5 ns 到 −0.38 ns，达 100 MHz 的 96%）

| 轮次 | 改动 | WNS | 逻辑级数 | 最差路径 |
|---|---|---|---|---|
| 0 | 初版 | **−22.528 ns** | 40 | `u_ln/cur_reg` → `u_svm` DSP |
| 1 | `logval` 切两级流水 | **−5.123 ns** | 18 | `u_ln/cur_reg` → `u_svm` DSP |
| 2 | 实现（place & route） | **−3.677 ns** | 18 | `u_ln/cur_reg` → `k_r` LUT 地址 |
| 3 | `msb_pos` 改分级前导零 | **−3.169 ns** | 16 | `g_dc[2]/x_prev` → `dc` DSP |
| 4 | `dc_block` 加减/乘法切两级 + 累加器 48→32 位 | **−2.606 ns** | 17 | `u_svm` 累加器 |
| 5 | `svm` 累加器 64→40 位 + 饱和改高位归约 | **−1.556 ns** | 14 | `u_svm/acc_q231` → `d_reg` |
| 6 | `svm` 乘加树与「加偏置+饱和」切两级 | **−0.661 ns** | 9 | `dc.acc` → `biquad` DSP |
| 7 | `dc`/`biquad`/`csp` 饱和改高位归约 | **−0.377 ns** | 10 | `u_ln/k_r` → `logval_r` |
| 8 | `lognorm` 插值再切一级 | **−0.476 ns** | — | 布局布线抖动 |

> **第 8 轮没有继续变好**（−0.377 → −0.476），说明已经到达
> **place & route 的抖动底线**（实测轮间波动 ±0.1 ns 量级）。
> 再切流水线的收益已经小于布线随机性，**继续微调性价比很低**。

**★ 反复出现的模式：宽位饱和比较**

第 5、7 两轮修的是**同一个问题**：`(y_ext > 上限) ? 上限 : ...` 这种写法
综合出来是一个**全宽减法器**（64 位要 16 级 CARRY4）。
改成对 `y_ext[MSB-1 : DW-1]` 做归约判断溢出后，一举降到 4 级。

同一个手法在四处都用上了：`dc_block` / `biquad_bank` / `csp_project` / `svm_core`。
**以后写定点饱和，直接写高位归约，不要写全宽比较。**

### 4.3 配置一致性核对（与 MATLAB 验证严格对齐）

用户提出硬件应与 MATLAB 验证使用**相同的通道数**。核对结果：

| 参数 | MATLAB / 参数文件 | 硬件（`eeg_csp_svm_top.v`） | 一致？ |
|---|---|---|---|
| `n_ch` | **3**（BCI IV 2b：C3/Cz/C4） | `N_CH = 3` | ✓ |
| `n_band` | 3（8-16 / 16-24 / 24-32 Hz） | `N_BAND = 3` | ✓ |
| `n_comp` | 2 | `N_COMP = 2` | ✓ |
| `n_feat_all` | 6 | `N_BAND*N_COMP = 6` | ✓ |
| `feat_dim` | 4（MIBIF 选中） | `FEAT_DIM = 4`，索引 [1,2,4,3] | ✓ |
| `win_len` | 626 | `WIN_LEN = 626` | ✓ |
| `fs` | 250 Hz | 250 Hz（4 ms/样本） | ✓ |
| `dc_a_q1_15` | 32361 | `16'sd32361` | ✓ |

**唯一的 8 出现在 `pkt_parser #(.N_CH(8))`** —— 这是对的：
Cyton 的包格式**固定携带 8 个通道**，我们只取前 3 个送进算法。
协议层是 8、算法层是 3，两者不矛盾。

> **这条核对解掉了一个重要顾虑**：之前担心"DSP 194（69%）只是 3 通道，
> 换 8 通道要 240 个 DSP 会超"。既然设计目标就是 3 通道，
> **DSP 69% 能装下，不构成阻塞**。P2 从阻塞项降级为"仅在未来扩展通道数时才需要考虑"。

### 4.4 最终时序（实现后）

| 项 | 实测 | 占器件 |
|---|---|---|
| Slice LUTs | ≈8400 | 15.8% |
| Slice Registers | ≈2500 | 2.4% |
| Block RAM | 0.5 tile | 0.36% |
| **DSP48E1** | **194** | **69%** |
| **WNS (setup)** | **−0.377 ns**（最好一轮） | 达 100 MHz 的 **96.2%** |
| WHS (hold) | +0.068 ns | ✓ |
| **Critical Warnings** | **0** | ✓ |

**尚未完全收敛，差 0.38 ns。**

### 4.5 ★★ 收尾：PLL 锁相环产生 95 MHz + locked 作为系统复位

**WNS 最终 = +0.119 ns（MET，达到 > 0.05 的目标）**

#### 方案

在 PL 内用 MMCM 把 PS 的 100 MHz 变成 95 MHz，并用 `locked`
作为系统初始复位。

| 项 | 值 |
|---|---|
| 频率规划 | VCO = 100 × **19** / **2** = **950 MHz**；CLKOUT = 950 / **10** = **95 MHz** |
| 约束校验 | VCO 950 ∈ [600,1440] ✓；PFD 50 ∈ [10,500] ✓；MULT 19 ∈ [2,64] ✓ |
| UART 影响 | DIV = 95e6/115200 = **824**；波特率 115291，误差 **+0.079%**（要求 ±2%，余量 25 倍） |

**为什么用 PL 内 MMCM 而不是直接改 PS 的 FCLK_CLK0：**
两者电气等价，但前者有两个无法替代的好处：
① 给出 `locked` 信号（故障检测 + 复位）；
② 时钟参数写在 RTL 里，**换板子不用改 PS7 的 IP 配置**——
本项目的硬约束是 ZYNQ7020-484 与 PYNQ-Z2 双板兼容。

#### 复位架构：异步置位、同步释放

```verilog
wire arst_n = pll_locked & rst_n_ext;
reg [3:0] rst_sync;
always @(posedge clk_95 or negedge arst_n)
    if (!arst_n) rst_sync <= 4'b0000;
    else         rst_sync <= {rst_sync[2:0], 1'b1};
wire rst_n = rst_sync[3];
```

上电时 MMCM 输出未稳定，**不能**让逻辑开始跑。
用 `locked` 异步置位、再用 95 MHz 同步释放，
避免复位撞除时的亚稳态。4 级同步器给 MMCM
锁定后的时钟留出足够的稳定时间。

> ★ 实现上用 `MMCME2_BASE` 原语而不是 clk_wiz IP：
> 功能完全一样（clk_wiz 本身就是它的封装），但不需要生成 IP、
> 不涉及版本升级，而且 **`SIMULATION` 宏下走行为模型，
> iverilog 全流程仿真照样能跑**。换成 clk_wiz 会让仿真链断掉。

#### ★★ 关键认识：WNS 为什么一直停在 0 附近

切到 95 MHz 后，WNS 仍只有 **+0.006 / +0.015 ns**。
原因不是路径压不下去，而是：

> **Vivado 的布局布线是“满足约束即停”。**
> 约束是 95 MHz 时，它优化到 WNS ≈ 0 就收手——并不是没有余力。

**解法：显式预留余量**

```tcl
set_clock_uncertainty -setup 0.050 [get_clocks clk_95]
```

相当于把目标抬高 0.05 ns，工具会继续优化直到真正留出这段余量。
结果：**+0.015 → +0.119 ns**。

这也是工程上的正确做法：WNS = 0.006 ns 的设计虽然“MET”，
但温度/电压漂移一点就会挂；留 0.05 ns（约 0.5%）才有实际可靠性。

#### 最终实测

| 项 | 实测 | 占器件 |
|---|---|---|
| Slice LUTs | 7688 | 14.45% |
| Slice Registers | 3572 | 3.36% |
| Block RAM | 0.5 tile | 0.36% |
| **WNS (setup)** | **+0.119 ns** | ✅ **达标** |
| WHS (hold) | +0.035 ns | ✓ |
| Critical Warnings | 0 | ✓ |

关键路径已转移到 `u_bank` 的 biquad 反馈环，
Data Path 7.104 ns（logic 3.690 + route 3.414）——比之前的
10.4 ns 已有充分余量。

#### 复现

```powershell
cd zynq\pl\synth
& 'D:\Xilinx\2025.1\Vivado\bin\vivado.bat' -mode batch -source synth_eeg.tcl
python parse_reports.py
```

---
### 4.6 ★★ 一个把综合数据"骗"过去的 bug：多驱动

在把 `win_var` 的存储拆成 `g_mem` 逐分量存储器后，主 always 块的复位分支里
**还留着 `z_d[k] <= 0; z_old[k] <= 0;`**，于是这两个信号有了两个驱动源。
Vivado 报：

```
CRITICAL WARNING: [Synth 8-6859] multi-driven net on pin Q
  with 1st driver pin 'i_0/u_win/z_d_reg[0][23]/Q'
```

**危险的地方在于"好数据"是假的**：那一轮综合报出
`Slice LUTs 3879 (7.29%)`、`LUT as Memory 0`，
看起来像是"BRAM 推断成功了、资源大降"——
**实际上是冲突的驱动被综合器合并掉，`mem` 数组整个被优化没了。**

修掉多驱动后真实数据回到 `LUT 8277 / LUT as Memory 1728`。

**教训**：
1. **综合报告里的"改善"要先看有没有 CRITICAL WARNING。**
   多驱动、锁存器推断这类告警会让工具算出无意义的结果。
2. **仿真器不报多驱动。** iverilog 对这版代码一路通过、逐位一致，
   因为仿真器的多驱动解析规则和综合器不同。
3. 每次综合后**先把 CRITICAL WARNING 数清零**，再看资源与时序。

### 4.7 综合暴露的"仿真通过 ≠ 能综合"

**Vivado 的 Verilog 模式不支持数组端口**：

```
[Synth 8-9917] port 'ch_data' must not be declared to be an array
```

本项目大量使用 `output wire signed [23:0] y [0:N-1]` 这类**数组端口**，
这是 SystemVerilog 特性。`iverilog -g2012` 默认按 SV 处理，仿真一直正常，
**直到综合才暴露**。

**修法**：Tcl 里 `set_property file_type SystemVerilog [get_files *.v]`。

**教训**：**仿真器宽容的地方，综合器不一定宽容。**
第一次跑综合之前，不要假设"仿真过了就能综合"。

另外 `win_var` 还返工过一次：第一版扁平数组 + 组合读，仿真完全正确，
但 Vivado 报 `[Synth 8-3391] Unable to infer a block/distributed RAM`。
改成"同址同沿读写"后又能推断 —— 而改成"读在写后一拍"后仿真就错了
（`sz` 恰好等于上一个采样点的值）。**BRAM 的 read-first 语义必须严格遵守。**

### 4.8 复现

```powershell
cd zynq\pl\synth
# 需要先把 log2_lut.hex 放在此目录（或从 sim 目录复制）
& 'D:\Xilinx\2025.1\Vivado\bin\vivado.bat' -mode batch -source synth_eeg.tcl
python parse_reports.py
```

产物在 `report_synth/`（已被 .gitignore 排除）。

---

## 5. 验证结果汇总

### 5.1 功能仿真

| 测试台 | 项数 | 结果 |
|---|---|---|
| `tb_uart_rx.v` | 9 | ✅ 全过（正常字节 ×5、毛刺抑制、帧错误、背靠背 ×10） |
| `tb_pkt_parser.v` | 23 | ✅ 全过（合法包、垃圾前缀、假 BOP、包尾非法、间隔超时、序号跳变、连续包） |
| `tb_biquad_bank.v` | 808 样本 | ✅ **逐位一致** |
| **`tb_cosim.v`（联合仿真）** | **744 包** | ✅ **协议样本 + biquad 输出均逐样本一致（最大差 0）** |

`tb_pkt_parser` 最终统计（与预期完全吻合）：

```
cnt_pkt = 9   （T1 合法包 2 个 + T2a 1 个 + T2b 1 个 + T4 恢复 1 个
                + T5 1 个 + T6 3 个 = 9）
footer_err = 2（T2b 假 BOP 错位包 + T3 非法包尾）
sync_err   = 1（T4 人为插入 2.5 字节间隔）
drop       = 24（T2b/T3/T4 故意丢弃的包造成的序号跳变累计）
```

### 5.2 PS 侧

| 项 | 结果 |
|---|---|
| 交叉编译 | ✅ `aarch64-linux-gnu-gcc 13.3.0`，`-Wall -Wextra` 零警告 |
| 产物 | `eeg_ps_aarch64`，elf64-littleaarch64，74 KB |
| EA 折叠恒等式 | ✅ 误差 $3.6\times10^{-15}$ ~ $7.3\times10^{-14}$ |
| Jacobi vs numpy `eigh` | ✅ 最大差 $1.6\times10^{-10}$ |
| 量化 | ✅ 归一化后零饱和，特征误差 2.5e-4 < 规格 1e-3 |

### 5.3 未验证项（诚实清单）

| 项 | 状态 | 风险 |
|---|---|---|
| **Vivado 综合的时序/资源** | ⏳ 未跑 | 中——§3.3 的资源是估算值 |
| `csp_project` 功能仿真 | ⏳ 只做了语法检查 | 中——公式简单但未逐位对拍 |
| `win_var` 功能仿真 | ⏳ 只做了语法检查 | 中——"加新减旧"的边界（窗口未满、指针回绕）需专门测 |
| `axil_regs` / 顶层 | ⏳ 未写 | 高——需要先定 BD |
| Block Design | ⏳ 未建 | 高——AXI 基地址未定，`eeg_regs.h` 里是占位值 |
| 上板实测 | ⏳ 未做 | 高——引脚需按丝印核对 |
| PL 侧 `raw_dump`（校准旁路） | ⏳ 未写 | 中——PS 校准目前只能走 `--sim-calib` |

---

## 6. 下一步

| # | 事项 | 前置 |
|---|---|---|
| 1 | 建 Vivado Block Design（PS7 + AXI Interconnect + 本 RTL），**确定 AXI 基地址** | XDC 引脚核对 |
| 2 | 补 `csp_project` / `win_var` 的功能仿真 | — |
| 3 | 跑综合，把 §3.3 的估算换成实测，并按时序报告优化 | 1 |
| 4 | 写 PL 侧 `raw_dump` 模块，打通 PS 校准链路 | 1 |
| 5 | 上板：先环回（TX 接 RX）验 UART，再接 Cyton 验包解析 | 1 |
| 6 | 用 `params/golden_vectors.mat` 做 PL vs PS-C 四级对拍 | 5 |

---

## 7. 一句话总结

**5 个问题里 4 个是"静默错误"**——不报错、不崩溃，只是结果悄悄不对：

| # | 问题 | 为什么静默 |
|---|---|---|
| 1 | 超时单位错 | 只是收不到包，看起来像"没接线" |
| 2 | 超时计数器不清零 | **第一个包永远对**，只有超时后才暴露 |
| 3 | 级联使能错位 | 输出"看起来像"滤波结果，只是整体移了一拍 |
| 4 | EA 权重溢出 | **3 通道完全正常**，8 通道才炸 |
| 5 | 测试台多驱动 | 结果随机，像"仿真器的问题" |

**共同教训**：**边界用例比正常用例重要得多。**
问题 2 需要"先制造一次超时"，问题 4 需要"换 8 通道"才会出现——
如果测试只跑正常路径，这五个问题一个都发现不了。
