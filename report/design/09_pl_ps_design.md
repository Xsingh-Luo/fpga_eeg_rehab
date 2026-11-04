# 09 · PL / PS 设计文档

> 上游：[02 算法实现规格书](02_algorithm_spec.md)（逐级算法）· [05 移植交接文档](05_fpga_porting_handoff.md)（任务分配）·
> [01 定点格式与接口约定](01_fixed_point_interface.md)（位宽契约）
> 代码：`zynq/pl/rtl/`（Verilog）· `zynq/ps/`（C）· `zynq/pl/sim/`（测试台）
> 数值以 [07 验证结果与最终配置](07_verification_and_config.md) 为准
> 版本 v1.0

---

## 0. 双板兼容策略（本设计的硬约束）

要求：**当前用 ZYNQ7020-484 验证，后续可能用 PYNQ-Z2 演示，两块板要最大程度兼容。**

| 项 | ZYNQ7020-484 | PYNQ-Z2 | 怎么统一 |
|---|---|---|---|
| 芯片 | `xc7z020clg484-2` | `xc7z020clg400-1` | **RTL 完全不含器件信息** |
| PL 时钟 | 厂商配置 | 板载 H16 = 125 MHz | **两块板都用 PS 的 `FCLK_CLK0 = 100 MHz`** |
| PL 引脚 | BANK35 排针 | PMODA | **只换 XDC，端口名不变** |
| PS 系统 | PetaLinux | PYNQ（也是 Linux） | **PS 代码用可移植 C + `/dev/mem`** |
| AXI 基址 | Vivado 分配 | Vivado 分配 | **`eeg_regs.h` 里的 `EE_BOARD_*` 一段** |
| 寄存器映射 | — | — | **完全一致（契约，不许改）** |
| 定点格式 | — | — | **完全一致（契约，不许改）** |

**具体做法：**

```
zynq/
├── pl/
│   ├── rtl/          板级无关 —— 两块板共用，一行不改
│   ├── sim/          板级无关
│   └── constr/
│       ├── eeg_csp_svm_zynq484.xdc   ← 唯一的板级差异
│       └── eeg_csp_svm_pynqz2.xdc    ← 唯一的板级差异
└── ps/
    ├── eeg_regs.h    ← 上半：寄存器映射（契约）；下半：EE_BOARD_*（唯一允许不同）
    ├── axil.c/h      ← /dev/mem 访问，任何 Zynq Linux 都能跑
    ├── ea.c/h        ← EA 数学，纯 C + libm
    └── eeg_ps.c      ← 主流程
```

> **为什么时钟必须统一用 FCLK_CLK0 = 100 MHz**
> RTL 里的 UART 分频系数（868）和超时阈值（17360）都按 100 MHz 写死。
> PYNQ-Z2 的板载晶振是 125 MHz，拿它当时钟会让波特率偏 +25%，
> 现象是**完全收不到数据**（不是数据错），极易误判成接线问题。
> 统一走 PS 的 FCLK_CLK0 后两块板行为完全一致。

> **为什么 PS 用 `/dev/mem` 而不是 `pynq.MMIO`**
> `pynq.MMIO` 会把代码绑死在 PYNQ 上。本项目 PS 只需要读写十来个 32 位寄存器，
> `mmap` 三十行就够，而且 PetaLinux 和 PYNQ 两边都有 `/dev/mem` —— 是最小公倍数。

---

## 1. 顶层数据流

```
Cyton D11/D12 ──[数字隔离器]──► PL
                                │
   ┌────────────────────────────▼─────────────────────────────────┐
   │ M1 uart_rx   → M2 pkt_parser → M3/M4 dc+biquad_bank(3带×2节) │
   │                                    ↓ x_b[0..2]               │
   │ M5 csp_project (W_eff 已含 EA 折叠) → z[0..5]                │
   │                                    ↓                          │
   │ M6 win_var (滑动窗加新减旧) → Sz/Sz2 → M7 lognorm → f[6]     │
   │                                    ↓                          │
   │ M8 svm_core → 标签 + 决策值        M9 axil_regs ◄──► PS      │
   └───────────────────────────────────────────────────────────────┘
```

---

## 2. PL 侧逐模块设计

### M1 `uart_rx.v` —— 异步串口接收

**功能**：把 115200 8N1 字节流还原成 `byte + byte_valid`。

**对应公式 / 参数推导**

| 量 | 公式 | 值 |
|---|---|---|
| 位周期 | $\lfloor f_{clk}/f_{baud} \rfloor$ | $\lfloor 10^8/115200 \rfloor = 868$ |
| 实际波特率 | $f_{clk}/868$ | 115207.4（误差 **+0.006%**） |
| 半位周期 | $\lfloor 868/2 \rfloor$ | 434 |

**为什么这么设计**

1. **不需要小数分频**：+0.006% 的误差远小于 UART 能容忍的 ±2~3%，取整即可。
2. **采样在位中点**：起始位下降沿后延时 **1.5 个位周期**才采第 0 位，之后每 1 个位周期采一位。
   采样点落在每位正中，对时钟偏差和线缆抖动容忍度最大。
3. **两级同步器 + 三级多数表决**：`uart_rx` 是异步输入，直接进状态机会有亚稳态（表现为随机位错）。
   三级一致才认边沿，滤掉 1~2 个时钟的窄毛刺——隔离器 + 长杜邦线很容易引入这种毛刺。

**验证**：`tb_uart_rx.v`，9 项全过（含毛刺抑制、帧错误、背靠背 10 字节）。

---

### M2 `pkt_parser.v` —— 33 字节包解析

**功能**：从字节流中同步出 Cyton 包，拆出 8 通道 24 位样本。

**包格式**（取自固件源码 `OpenBCI_32bit_Library.cpp`，非二手资料）

| 索引 | 内容 |
|---|---|
| 0 | `0x41`（`OPENBCI_BOP` = `'A'`） |
| 1 | `sampleCounter` 0~255 回绕 |
| 2~25 | 8 通道 × 3 字节，24 位有符号，**大端**（MSB 先到） |
| 26~31 | AUX 6 字节（默认加速度计，本设计忽略） |
| 32 | `0xC0 \| packetType`，校验 `(b & 0xF0) == 0xC0` |

**为什么这么设计**

1. **不缓存整包**：只用一个 6 位索引 + 8 个 24 位通道寄存器，边收边按索引塞进对应通道。
   → **0 BRAM / 0 LUTRAM**。索引到通道号的映射是 $c = \lfloor (idx-2)/3 \rfloor$，
   字节位置是 $(idx-2) \bmod 3$（0=MSB）。
2. **失步恢复靠三重判断**：`0x41` 也可能出现在数据里，只搜同步字会误锁。必须：
   - 33 字节定长
   - 包尾高位校验 `(b & 0xF0) == 0xC0`
   - 字节间隔超时 $T_{out} = 10 \cdot T_{bit} \cdot 2 = 17360$ 周期 ≈ 174 µs

   **超时阈值取 2 个字节时间**的依据：33 字节是**连续突发**发送的
   （2.864 ms 发完，然后空闲 1.136 ms），包内不会出现 >2 字节的空隙。
3. **丢包检测靠序号**：$\text{drop} \mathrel{+}= (seq_{new} - seq_{prev} - 1) \bmod 256$。
   ⚠️ 序号只有 1 字节，一次丢超过 255 个样本无法识别——协议限制。

**验证**：`tb_pkt_parser.v`，23 项全过。**仿真中发现 2 个真 bug（见调试报告 §2）**。

---

### M3/M4 `biquad_bank.v` —— 3 子带滤波器组

**功能**：去 DC + 3 个子带（8-16 / 16-24 / 24-32 Hz）带通，每带 2 节。

**对应公式（转置直接 II 型）**

$$y[n] = b_0 x[n] + s_1[n-1]$$
$$s_1[n] = b_1 x[n] - a_1 y[n] + s_2[n-1]$$
$$s_2[n] = b_2 x[n] - a_2 y[n]$$

**为什么用转置型而不是直接 I 型**

| | 转置直接 II 型 | 直接 I 型 |
|---|---|---|
| 反馈路径 | `s1 → acc → y → (×a1) → s1`，**1 次乘加** | `y → y1 → y2`，**两级累加** |
| 状态数 | 2 / 节 | 4 / 节 |
| 100 MHz 时序 | 容易收敛 | 关键路径长，要额外打拍 |

**定点格式**（依据 [01 号文档](01_fixed_point_interface.md) §2）

| 量 | 格式 | 位宽 | 说明 |
|---|---|---|---|
| `x` | Q8.15 | 24 | 输入 |
| `b, a` | **Q3.14** | **18** | 不用 Q2.14：节 4/5 的 $b_1 \approx -1.99$，16 bit 下只剩 0.3% 余量 |
| `s1, s2` | Q11.29 | 48 | 与 $b \cdot x$ 同量纲 |
| `y` | Q8.15 | 24 | `>>14` 后饱和 |

**为什么 3 带 × 2 节而不是单带 5 节**

FBCSP 子带**故意重叠**，不需要陡峭截止 → 每带 4 阶巴特沃斯只要 2 节。
3 带 × 2 节 = 6 个 biquad/通道，比单带 Cheby2 10 阶（5 节）只多 20%，
但实测准确率高 **+2.9 个百分点**。

**运算量**：$N \times 3 \times 2 \times 5 = 30N$ MAC/采样点。N=8 时 240 MAC，
在 4 ms 采样周期（400,000 周期 @100 MHz）里只占 240 周期 → **可时间复用到 2~4 个 DSP**。

**验证**：`tb_biquad_bank.v` + `gen_biquad_tv.py`，**808 个样本逐位一致 ✓**。

---

### M5 `csp_project.v` —— CSP 空间投影

**对应公式**

$$z_j = \sum_c W_{\text{eff}}[c][j] \cdot x[c], \qquad j = 0 \dots n_{comp}-1$$

**★ 为什么 EA 能折进来（本项目最值钱的一条）**

$$z = \tilde W^T (M x) = (M^T \tilde W)^T x = W_{\text{eff}}^T x$$

白化矩阵在 PS 上离线乘进 CSP 投影后，**推理链路完全不感知 EA 的存在，PL 零改动**。

**定点格式**

| 量 | 格式 | 位宽 |
|---|---|---|
| `x` | Q8.15 | 24 |
| `W_eff` | **Q1.14（归一化后）** | **16** |
| 乘积 | Q9.29 | 42 → 累加 48 |
| `z` | Q10.13 | 24（`>>16` 后饱和） |

**★ 为什么是 Q1.14 而不是 Q3.14 —— 一个必须解释的变更**

EA 折叠把权重动态范围抬高了：

| 通道数 | $\lvert \tilde W \rvert_{\max}$ | $\lvert W_{\text{eff}} \rvert_{\max}$ | 直接 Q3.14/18bit（±8.0） |
|---|---|---|---|
| 3（BCI IV 2b） | < 2.0 | **3.81** | ✓ 刚好够 |
| 8（自采 Cyton） | ≈ 2.2 | **19 ~ 20** | **✗ 饱和 5~10 个权重** |

**解法**：特征定义本身就允许免费缩放 ——

$$f_i = \log\frac{\text{var}(z_i)}{\sum_j \text{var}(z_j)}$$

$W \to \alpha W \Rightarrow z \to \alpha z \Rightarrow \text{var} \to \alpha^2\text{var} \Rightarrow$ **比值不变** ⇒ $f$ 完全不变。

所以先把每个带的 $W_{\text{eff}}$ 归一化到 $\max = 0.875$ 再量化：

| 方案 | 饱和数（8 通道） | 特征误差 | 系数位宽 |
|---|---|---|---|
| A 直接 Q3.14/18bit | **5 ~ 10** ✗ | — | 18 |
| **B 归一化 + Q1.14/16bit** | **0** ✓ | **2.5e-4** | **16** |

规格要求特征相对误差 < 1e-3（[01 号文档](01_fixed_point_interface.md) §3.4）→ **方案 B 满足且更省资源**。

复现：`python zynq/ps/verify_ea_math.py`

**为什么全并行**：$N \times n_{comp}$ 个乘法器。时间复用只要十几个周期，但那样 CSP 就得缓存
3 个带的样本、增加握手逻辑；而"每 4 ms 只有一个采样点"的节拍下，
用满并行换**最简控制流**是划算的。（资源紧张时可按 [07 号文档](07_verification_and_config.md) §4.3 改时分复用。）

---

### M6 `win_var.v` —— 滑动窗运行和

**对应公式**

$$S_z = \sum_{n=0}^{T-1} z[n], \qquad S_{z^2} = \sum_{n=0}^{T-1} z[n]^2$$

$$\text{var} = \frac{S_{z^2} - S_z^2/T}{T}$$

**★ 核心技巧：加新减旧**

$$S_z \leftarrow S_z + z[n] - z[n-T], \qquad S_{z^2} \leftarrow S_{z^2} + z[n]^2 - z[n-T]^2$$

每来一个采样点只要 2 次加、1 次减、1 次平方 —— **O(1)，与窗口长度无关**。

> **如果不用这个技巧会怎样**：每拍重算 T=626 个样本 → 决策更新率从
> **250 Hz 掉到 0.4 Hz**，整个"每 4 ms 一个决策"的卖点就没了。

**★ 为什么用整数累加**

加新减旧在无限精度下精确；用整数表示就没有舍入误差 → **理论零漂移**。
若用浮点，每拍两次舍入，626 点窗跑几小时后 $S_z$ 会缓慢漂走。

**存储**：环形缓冲 $n_{comp} \times T \times 24$ bit。$n_{comp}=6$、T=626 → 90 kbit → **3 个 BRAM36**。

---

### M8 `svm_core.v` —— 线性 SVM 判决

**对应公式**：$d = \mathbf{w} \cdot \mathbf{f} + b$，标签 $= [d > 0]$

**为什么线性核能折叠**：$d(\mathbf f) = \sum_i \alpha_i y_i (\mathbf{s v}_i \cdot \mathbf f) + b
= \left(\sum_i \alpha_i y_i \mathbf{s v}_i\right) \cdot \mathbf f + b = \mathbf w \cdot \mathbf f + b$

折叠后 PL 只需存 **4 个权重 + 1 个偏置**（不是 261 个支持向量）。
实测折叠前后决策值最大差 $1.8\times10^{-13}$，标签完全一致。

---

## 3. PS 侧逐模块设计

### 3.1 职责边界

> **PS 只做 PL 做不了的事。** 不碰实时数据通路 —— 所以 PS 即使被调度、被 I/O 阻塞，
> 也不会丢样本、不会引入抖动。

| # | 职责 | 为什么必须在 PS |
|---|---|---|
| ① | **校准（EA）** | 需要矩阵开方 $M=\bar R^{-1/2}$，PL 做特征分解不划算 |
| ② | 参数管理 | 解析、CRC 校验、回读确认——控制流 |
| ③ | 结果读取 | 轮询寄存器、加时间戳、入队 |
| ④ | 可视化 | 需要 OS 与图形库 |
| ⑤ | 数据记录 | 需要文件系统 |
| ⑥ | 范式呈现（cue） | 屏幕/LED 时序；**与数据同源，免硬件触发同步** |
| ⑦ | 状态监控 | 丢包数、错误标志、`PERF_CNT` |

> ① 不是打杂：它是**唯一需要矩阵分解的环节**，而且实测带来 **+2.94 pp** 的精度增益。

### 3.2 `ea.c` —— 欧氏对齐

**对应公式**

$$\bar R = \frac{1}{n}\sum_{i=1}^{n} \frac{x_i x_i^T}{\text{tr}(x_i x_i^T)}, \qquad M = \bar R^{-1/2}, \qquad W_{\text{eff}} = M^T \tilde W$$

**三个关键设计决定**

| 决定 | 依据 |
|---|---|
| **迹归一化** $\div \text{tr}(x_ix_i^T)$ | 不同试次整体幅度不同（阻抗、被试状态），不归一化会让幅度大的试次主导 $\bar R$ |
| **算术均值，不用黎曼均值** | 实测算术均值与黎曼均值结果**完全相同**，省掉迭代与收敛判断 |
| **Jacobi 手写特征分解** | 维度只有 3~8，Jacobi 约 6~10 轮收敛、60 行代码、不引入 LAPACK/BLAS 依赖 |

**特征值下限**：$\varepsilon = \text{tr}(\bar R)/n \times 10^{-6}$。
脑电协方差在坏段（全零段/削顶段）可能接近奇异，不设下限会得到爆炸的权重。
触发截断时**打印警告**，不静默。

**★ 校准数据量**：20 个试次（约 1 分钟）。5 个明显不够（负增益），10 个不稳定
（实测 B08 在 5 试次时掉 14 pp，10 试次才恢复）。

**★ 校准必须与正式任务同一个 session**：把 04E+05E 拼起来取前 N 个做校准是错的——
N=160 时校准集是 04E、测试集是 05E，跨域误用，实测增益从 +2.94 pp 掉到 0 甚至为负。

### 3.3 `axil.c` —— AXI-Lite 访问

**为什么用 `/dev/mem` 而不是内核驱动**：只需要读写十来个 32 位寄存器，
不涉及 DMA/中断/并发。字符设备驱动要 200+ 行内核代码 + 编译进内核 + 设备树节点，
而 `mmap` 只要 30 行用户态代码。**控制流复杂度换来的收益为零。**

**参数下发协议**（与 [01 号文档](01_fixed_point_interface.md) §5.3 一致）

```
① 写 PARAM_ADDR = (block_id << 24) | n_words
② 逐字写 PARAM_RAM
③ 写 PARAM_COMMIT = 1
④ 轮询 STATUS.params_crc_ok
```

> **失败必须可见**：CRC 不通过时返回错误、由上层**停机**，而不是继续跑。
> 静默使用半截参数会导致"精度莫名其妙掉了"这种最难查的 bug。

---

## 4. 接口契约（两块板必须完全一致）

### 4.1 寄存器映射

| 偏移 | 名称 | R/W | 说明 |
|---|---|---|---|
| `0x000` | `CTRL` | RW | [0]start [1]soft_reset [2]clear_accum [3]dbg_en |
| `0x004` | `STATUS` | RO | [0]busy [1]params_loaded [2]params_crc_ok [15:8]drop [23:16]sync |
| `0x008` | `MODE` | RW | 0=live 1=replay 2=inject |
| `0x00C` | `WIN_LEN` | RW | T（默认 626） |
| `0x010` | `N_CH` | RW | 3 / 8 / 16 |
| `0x014` | `FEAT_DIM` | RW | MIBIF 选中维数（默认 4） |
| `0x018` | `DECISION` | RO | [0]label [31:1] 决策值 Q8.23 |
| `0x020` | `PERF_CNT` | RO | 上一决策的 PL 处理周期数 |
| `0x024` | `ACC_CNT` | RO | 累计决策数 |
| `0x028` | `ERR_FLAGS` | RO | 四类错误计数 |
| `0x100+` | `PARAM_RAM` | WO | 参数写入窗口 |

### 4.2 三个维度陷阱（写代码时必须注意）

| 宏 | 值 | **不是** |
|---|---|---|
| `N_COMP` | $\min(2m, N)$ = **2** | 2m = 2 ✓ 但 3 通道 m=2 时是 3 |
| `N_FEAT_ALL` | `N_BAND × N_COMP` = **6** | 不是 `N_CH` |
| `FEAT_DIM` | MIBIF 选中数 = **4** | **既不等于 6 也不等于 3** |

**三个都必须做成可配的宏/寄存器，硬编码必错。**

---

## 5. 验证方法（四级对拍）

| 级 | 对拍对象 | 手段 | 状态 |
|---|---|---|---|
| 1 | 浮点算法 vs 文献 | MATLAB / Python 独立复现 | ✅ 已完成 |
| 2 | **定点 RTL vs 定点参考** | `gen_biquad_tv.py` 生成逐位模型 + `tb_biquad_bank.v` | ✅ **808 样本逐位一致** |
| 3 | **协议时序 vs 边界用例** | `tb_uart_rx.v`（9 项）+ `tb_pkt_parser.v`（23 项） | ✅ 全过 |
| 4 | PL vs PS-C | 用 `params/golden_vectors.mat` 逐级 dump | ⏳ 待做 |

**每级都要 dump 中间结果，不能只看最终准确率** —— 否则定位不到是哪一级错了。

---

## 6. 复现

```powershell
# PL 仿真
cd zynq\pl\sim
python gen_biquad_tv.py
iverilog -g2012 -o tb_bq.vvp tb_biquad_bank.v ..\rtl\biquad_bank.v ; vvp tb_bq.vvp
iverilog -g2012 -o tb_pkt.vvp tb_pkt_parser.v ..\rtl\uart_rx.v ..\rtl\pkt_parser.v ; vvp tb_pkt.vvp
iverilog -g2012 -o tb_uart_rx.vvp tb_uart_rx.v ..\rtl\uart_rx.v ; vvp tb_uart_rx.vvp

# PS 交叉编译 + EA 数学验证
cd zynq\ps
make check CROSS=D:\Xilinx\2025.1\gnu\aarch64\nt\aarch64-linux\bin\aarch64-linux-gnu-
python verify_ea_math.py
```
