# 05 · 算法 FPGA 移植交接文档

> 上游：[02 算法实现规格书](02_algorithm_spec.md)（算法定义）· [04 串口接入交接文档](04_uart_handoff.md)（数据入口）
> 下游：`matlab/`（已验证的参考实现）· `params/csp_svm_params.h`（已导出的参数）
> 本文档定义 **MATLAB 验证过的算法 → Zynq PS/PL 的任务分配与接口契约**
> 版本 v1.0

---

## 0. 交接范围

| 来自 | 内容 | 状态 |
|---|---|---|
| MATLAB | 完整算法链（去DC → 带通 → 分段 → CSP → 特征 → SVM） | ✅ 已验证：cross 73.98% / within 72.05% |
| MATLAB | 定点化方案（Q 格式、溢出检查） | ✅ 已验证：定点 vs 浮点仅 **−0.05 个百分点** |
| MATLAB | FPGA 参数导出 | ✅ 已产出 `params/csp_svm_params.h` |
| 待做 | 上述算法在 Zynq 上的 PS/PL 实现 | ← **本文档的任务** |

---

## 1. PS / PL 任务分配（核心表）

**分配原则**：PL 做**流式、规则、需确定性**的处理；PS 做**控制流、矩阵分解、交互**；PC 做**离线训练**。三者不重复劳动。

| # | 任务 | **PL** | **PS** | PC |
|---|---|:---:|:---:|:---:|
| **A. 数据入口** | | | | |
| A1 | UART 接收（115200，33 字节包） | ✅ | | |
| A2 | 包解析 / 校验 / 丢包统计 | ✅ | | |
| **B. 信号处理（实时推理链）** | | | | |
| B1 | 去直流（一阶高通 0.5 Hz） | ✅ | | |
| B2 | 带通：3 子带滤波器组（8–16/16–24/24–32 Hz，每带 2 节 biquad） | ✅ | | |
| B3 | 每 trial 去均值 | ✅ | | |
| B4 | CSP 空间投影 `z = Wᵀx` | ✅ | | |
| B5 | 滑动窗运行和 `Σz, Σz²` | ✅ | | |
| B6 | 归一化 + log → 特征 `f` | ✅ | | |
| B7 | 线性 SVM 判别 `d = w·f + b` | ✅ | | |
| **B8. 欧氏对齐（EA）** ★ | | | |
| B8a | 校准数据采集（**20 个试次，约 1 分钟**） | | ✅ | |
| B8b | `R̄_b` → `M_b = R̄_b^(-1/2)`（逐带 3×3 或 8×8 特征分解） | | ✅ | |
| B8c | **折叠 `W_eff = M_bᵀ·W̃_b`** | | ✅ | |
| B8d | `W_eff` 下发（复用 D1 的通道） | ✅ | ✅ | |
| **C. 训练（离线）** | | | | |
| C1 | CSP 训练（协方差/正则化/白化/EVD） | | | ✅ |
| C2 | SVM 训练 + 线性核折叠 | | | ✅ |
| C3 | 定点量化 + 参数导出 + golden vector | | | ✅ |
| C4 | MIBIF 特征选择（导出索引表） | | | ✅ |
| **D. 参数与控制** | | | | |
| D1 | 参数下发协议 + CRC 校验 | ✅ | ✅ | |
| D2 | 换被试 / 换参数的切换流程（**含重新校准**） | | ✅ | |
| D3 | 软复位、状态监控 | ✅ | ✅ | |
| **E. 交互与记录** | | | | |
| E1 | 范式呈现（cue 时序：LED / 屏幕） | | ✅ | |
| E2 | 实时可视化（波形 / 特征 / 决策值曲线） | | ✅ | |
| E3 | 数据记录（原始 + 特征 + 决策 → SD/文件） | | ✅ | |
| E4 | 上位机通信（以太网 / 调试） | | ✅ | |
| **F. 进阶（P7 阶段）** | | | | |
| F1 | 协方差累加 `Σxxᵀ` | ✅ | | |
| F2 | 在线自适应 EVD → 新 W | | ✅ | |
| F3 | RBF 核 SVM（对比实验） | | ✅ | |

**一句话**：**PL 承担从引脚到标签的全部实时路径；PS 承担配置、交互、记录，以及（进阶）在线自适应的矩阵分解。**

---

## 2. PL 侧规格

### 2.1 顶层数据流

```
uart_rx ─► pkt_parser ─► dc_block ─► biquad_bank ─► csp_project
                                                        │
                          ┌─────────────────────────────┘
                          ▼
                       win_var ─► lognorm ─► svm_core ─► 标签/决策值
                          │            │          │
                          └────────────┴──────────┴──► axil_regs / dbg_bram ─► PS
                                       ▲
                                  AXI-Lite（参数下发、状态回读）
```

**核心原则：中间结果不出 PL。** 只有最终的「标签 + 决策值」回 PS（另加可选的调试旁路）。这是 00 号文档定下的规矩，也是延迟可控的前提。

### 2.2 逐模块规格

#### M1 `uart_rx`（RTL）
| 项 | 值 |
|---|---|
| 功能 | 异步串口接收，115200 8N1 |
| 位周期 | 868 时钟（100 MHz，误差 +0.006%） |
| 输入处理 | 两级同步器 + 3 采样毛刺滤波 |
| 输出 | `byte[7:0]`, `byte_valid`, `frame_err` |
| 资源 | ~40 LUT / ~30 FF |

#### M2 `pkt_parser`（RTL）
| 项 | 值 |
|---|---|
| 功能 | 搜 `0x41` → 收 33 字节 → 校验 `(tail & 0xF0)==0xC0` → 拆 8 通道 |
| 实现要点 | **不缓存整包**；边收边按索引塞进 8×24 bit 通道寄存器 |
| 失步恢复 | 33 字节定长 + 包尾校验 + 字节间隔超时（>174 µs）三重判断 |
| 输出 | `sample_valid`, `ch_data[0:7][23:0]`, `sample_seq[7:0]`, `err_flags[31:0]` |
| 资源 | ~150 LUT / ~280 FF / **0 DSP / 0 BRAM** |

#### M3 `dc_block`（HLS 或 RTL）
| 项 | 值 |
|---|---|
| 差分方程 | `y[n] = a·(y[n-1] + x[n] − x[n-1])` |
| 系数 | `a = 0.9876 @ Q1.15 = 32361`（截止 0.5 Hz @250 Hz） |
| 状态 | Q4.19 / 24 bit × N 通道 |
| 资源 | N × 2 个 DSP（或并入 M4 的时分复用引擎） |

#### M4 `biquad_bank`（HLS）★ 运算量最大的一级
| 项 | 值 |
|---|---|
| 滤波器 | **3 个子带并行**：每带 4 阶巴特沃斯 = 2 节 biquad |
| 结构 | **转置直接 II 型**（反馈链只 1 次乘加，时序好收敛） |
| 系数 | **Q3.14 / 18 bit**（16 bit 下 b1≈−1.99 余量不足） |
| 状态/累加器 | Q8.15 / 24 bit，累加器 **48 bit 饱和** |
| 运算量 | N × 5 节 × 5 MAC = **25N MAC/采样点**（N=8 → 200 MAC） |
| **实现策略** | **时间复用 MAC 引擎**：4 ms 内有 400,000 周期，200 MAC 只占 200 周期 → **2–4 个 DSP 足够** |
| 资源 | ~1.5K LUT / 2–4 DSP / 状态存 LUTRAM |

**系数（Q2.14，来自 `matlab/cheby2_bandpass_sos.m`）**
```c
{    348,      3,    348, -16006,   6810 },   // 节1
{  16384,      0, -16384, -21165,   6541 },   // 节2
{  16384, -13019,  16384, -19662,  12801 },   // 节3
{  16384, -32664,  16384, -29130,  13257 },   // 节4
{  16384, -32527,  16384, -31303,  15436 },   // 节5
```

#### M5 `csp_project`（HLS）
| 项 | 值 |
|---|---|
| 计算 | `z_j = Σ_c W[c][j] · x[c]`，j = 0..n_comp−1 |
| 维度 | **(N × n_comp) 个 MAC**；2b 是 3×3，自采 8 通道 m=2 → 8×4 |
| 权重格式 | **Q2.14 / 16 bit** |
| 输出 | `z` Q10.13 / 24 bit |
| **关键** | `W` 必须**支持运行时经 AXI-Lite 重载**（换被试不用重新综合） |
| 资源 | ~0.5K LUT / n_comp 个 DSP（3 或 4） |

**★ 特征维不是 2m**：`n_comp = min(2m, N)`。2b 只有 3 通道，`m=1` 得 **2** 个分量/带，3 带共 **6** 维，MIBIF 再选 **4** 维。**寄存器里要做成可配的**。

#### M6 `win_var`（RTL）★ 决定"每 4 ms 一个决策"的关键
| 项 | 值 |
|---|---|
| 机制 | 维护 `Sz`、`Sz²` 运行和，**每采样点加新减旧** |
| 方差 | `var = (Sz² − Sz²/T) / T`，全整数加减 → **理论零漂移** |
| 窗口 | T = **626**（[3.5, 6.0] s @250 Hz），可配 |
| 兜底 | 每 2 s 从零重算一次（可关） |
| 资源 | 环形缓冲 `n_comp × T × 24 bit` → 2b 时 **2 个 BRAM36**；8 通道 4 分量 → 4 个 |

#### M7 `lognorm`（HLS）
| 步骤 | 实现 |
|---|---|
| ① `S = Σ var_j` | n_comp−1 次加法 |
| ② `inv_S = 1/S` | Newton-Raphson，初值查表，**2 次迭代** |
| ③ `r_j = var_j · inv_S` | Σr = 1 |
| ④ `l2_j = log2(r_j)` | 前导零计数 + **512 点尾数表 + 线性插值** |
| ⑤ `f_j = l2_j · ln2` | **Q4.11 / 16 bit** |
| 资源 | ~0.5K LUT / 2 DSP / 1 BRAM（log 表） |

**★ 必须用归一化定义** `f_i = log(var_i / Σvar_j)`，不是 MNE 的 `log(var_i)`。
实测：前者对信号整体缩放**完全免疫**（Δ=4e−16），后者会平移 9.21。

#### M8 `svm_core`（HLS）
| 项 | 值 |
|---|---|
| 线性核（主线） | `d = w·f + b`，折叠后 **n_comp 个乘加** |
| 权重 | `w` Q4.11 / 16 bit，`b` Q8.23 / 32 bit |
| 输出 | 标签（d>0）+ 决策值 `d` |
| 资源 | **n_comp 个 DSP**（3 个）+ 加法树，延迟 < 100 ns |
| RBF 核 | **不放 PL**（见 §4.4） |

**实测折叠等价性**：折叠前后决策值最大差 `1.8e−13`，预测标签完全一致。
B01 有 261 个支持向量 → 折叠后 PL 只存 **3 个权重 + 1 个偏置**。

#### M9 `axil_regs`（RTL）
统一寄存器映射，见 §5.1。

#### M10 `dbg_bram`（RTL，可关）
中间结果 dump（滤波后信号 / z / var / f / dec），供 ILA 与 golden 对拍。**上线前可关**。

### 2.3 PL 资源预算

| 模块 | LUT | FF | DSP | BRAM36 |
|---|---|---|---|---|
| M1 `uart_rx` | 40 | 30 | 0 | 0 |
| M2 `pkt_parser` | 150 | 280 | 0 | 0 |
| M3 `dc_block` | 100 | 100 | (并入 M4) | 0 |
| M4 `biquad_bank` | 1500 | 1200 | **2–4** | 0 |
| M5 `csp_project` | 500 | 400 | **3–4** | 0 |
| M6 `win_var` | 800 | 600 | 0 | **2–4** |
| M7 `lognorm` | 500 | 400 | 2 | 1 |
| M8 `svm_core` | 400 | 300 | **3** | 0 |
| M9 `axil_regs` | 1200 | 1000 | 0 | 0 |
| M10 `dbg_bram` | 300 | 200 | 0 | 2 |
| **合计** | **~5.5K** | **~4.5K** | **~12** | **~7** |
| **Zynq-7020 总量** | 53,200 | 106,400 | 220 | 140 |
| **占用率** | **~10%** | **~4%** | **~5%** | **~5%** |

**余量充足**：升到 8 通道 + FBCSP 4 频带后，预计 DSP 到 60–120、BRAM 到 20–40，仍在射程内。

### 2.4 PL 内延迟预算

| 环节 | 周期 @100 MHz | 时间 |
|---|---|---|
| 第 33 字节到齐 → 样本可用 | ~5 | 50 ns |
| 去DC + 带通（时分复用 200 MAC） | ~210 | 2.1 µs |
| CSP 投影 | ~10 | 100 ns |
| 滑动窗更新 | ~5 | 50 ns |
| 归一化 + log | ~50 | 0.5 µs |
| SVM 判别 | ~10 | 100 ns |
| **合计（样本到 → 决策出）** | **~290** | **≈ 3 µs** |

**满足 < 10 µs 的目标**，且完全确定、无抖动。

---

## 3. PS 侧规格

### 3.1 职责

| 职责 | 说明 | 优先级 |
|---|---|---|
| **参数管理** | 解析 JSON/头文件 → 量化 → 经 AXI-Lite 下发 → CRC 校验 → 回读确认 | 必做 |
| **状态监控** | 轮询丢包数、错误标志、`PERF_CNT`（实测延迟证据） | 必做 |
| **结果读取** | 读标签 + 决策值 → 可视化 + 记录 | 必做 |
| **范式呈现** | cue 时序（LED / 屏幕）。**由 PS 产生 cue 可与数据同源，免同步** | 必做 |
| **数据记录** | 原始波形 / 特征 / 决策 / 时间戳 → SD 卡 | 推荐 |
| **换被试流程** | 软复位 → 清累加器 → 下发新参数 → 校验 | 推荐 |
| **在线自适应 EVD** | 读协方差累加 → Jacobi EVD → 新 W 下发（P7） | 进阶 |
| **RBF 核 SVM** | NEON 实现，做线性 vs RBF 对比实验（P7） | 进阶 |

### 3.2 软件架构（PYNQ / Linux）

```
┌─ 采集线程（高优先级）────────────────────────┐
│  轮询 AXI-Lite 的 DECISION 寄存器            │
│  → 标签 + 决策值 + 时间戳 → 环形队列          │
└──────────────────┬───────────────────────────┘
                   ▼
┌─ 记录线程 ────────┴───────────────────────────┐
│  批量写 SD 卡（不要在采集线程里写文件）        │
└──────────────────────────────────────────────┘
┌─ 显示线程（低优先级）─────────────────────────┐
│  波形 / 特征条形图 / 决策值曲线 d(t) / 准确率 │
└──────────────────────────────────────────────┘
┌─ 控制线程 ────────────────────────────────────┐
│  参数下发、模式切换、软复位                    │
└──────────────────────────────────────────────┘
```

**线程纪律**：采集线程绝不做文件 I/O、绝不做绘图。这两件事都会阻塞几十毫秒。

### 3.3 参数下发流程

```
1. 读 params/csp_svm_params.json
2. 校验版本号、维度（N_CH / N_BAND / N_COMP / FEAT_DIM / WIN_LEN）
3. 写 CTRL.soft_reset = 1、CTRL.clear_accum = 1
4. 分块写 PARAM_RAM：目标块 ID + 数据
5. 写 PARAM_COMMIT
6. 轮询 STATUS.params_crc_ok
   ├─ =1 → 进入运行
   └─ =0 → 报错停机，**继续用旧参数**（不要静默使用半截参数）
7. 回读校验：读回 W / w / b 与源文件逐值比对
```

---

## 4. PS↔PL 接口契约

### 4.1 AXI-Lite 寄存器映射

基地址示例 `0x43C0_0000`（以 Vivado Address Editor 为准）。

| 偏移 | 名称 | R/W | 位域 | 说明 |
|---|---|---|---|---|
| `0x000` | `CTRL` | RW | [0] start<br>[1] soft_reset<br>[2] clear_accum<br>[3] dbg_en<br>[4] zero_recalc_en | 软复位**必须**清空所有滤波器状态与运行和 |
| `0x004` | `STATUS` | RO | [0] busy<br>[1] params_loaded<br>[2] params_crc_ok<br>[7:3] reserved<br>[15:8] drop_cnt<br>[23:16] sync_err | 丢包计数实时可读 |
| `0x008` | `MODE` | RW | [1:0] 0=live 1=replay 2=inject | **决赛降级开关** |
| `0x00C` | `WIN_LEN` | RW | [15:0] 窗口点数 T（默认 **626**） | 改动后自动清累加器 |
| `0x010` | `N_CH` | RW | [7:0] 通道数 3/8/16 | 通道扩展实验用 |
| `0x014` | `FEAT_DIM` | RW | [7:0] 特征维 = min(2m, N)（默认 **3**） | ★ 不是 2m |
| `0x018` | `DECISION` | RO | [0] label<br>[31:1] 决策值 Q8.23 | **每采样点更新** |
| `0x01C` | `SVM_CFG` | RW | [0] kernel<br>[15:1] K | 预留 RBF 切换 |
| `0x020` | `PERF_CNT` | RO | 上一个决策的 PL 处理周期数 | **实测延迟的证据** |
| `0x024` | `ACC_CNT` | RO | 累计决策数 | 与 PS 收包数交叉验证 |
| `0x028` | `ERR_FLAGS` | RO | [7:0] footer_err<br>[15:8] sync_err<br>[23:16] drop_cnt<br>[31:24] frame_err | |
| `0x100–` | `PARAM_RAM` | WO | | 参数写入窗口 |

### 4.2 参数块 ID

| ID | 块 | 长度 |
|---|---|---|
| `0x01` | `IIR_SOS` | 5 × 5 × int32 |
| `0x02` | `DC_A` | 1 × int16 |
| `0x03` | `CSP_W` | N_CH × FEAT_DIM × int16 |
| `0x04` | `SVM_W` | FEAT_DIM × int16 |
| `0x05` | `SVM_B` | 1 × int32 |
| `0x06` | `WIN_LEN` | 1 × int32 |

### 4.3 中断

**默认关闭**。PS 用轮询 `DECISION` 寄存器即可（250 Hz 轮询成本极低）。
只有在线自适应（F1/F2）启用时，才打开「协方差累加完成」中断。

理由：中断在 250 Hz 下的开销和上下文切换会引入抖动，而这不是必需的。

### 4.4 明确**不**走 PS↔PL 的东西

| 内容 | 为什么 |
|---|---|
| 逐样本的 EEG 数据 | 中间结果不出 PL；且 250 Hz 逐样本 DMA 中断开销爆炸 |
| CSP 投影结果 `z` | 同上 |
| 特征 `f`（常规运行时） | 只有在线自适应才需要，走专门的批量通道 |
| RBF 核的中间量 | RBF 放 PS 时，特征通过批量 DMA 过去，**不逐样本** |

---

## 5. MATLAB → FPGA 逐级对应表（可追溯性）

| MATLAB | 行/函数 | PL 模块 | 定点格式 | 验证方式 |
|---|---|---|---|---|
| `filter([a -a],[1 -a], X, [], 3)` | `csp_svm_validate.m` `preprocess` | M3 `dc_block` | Q1.15 系数 | 逐样本对比 |
| `biquad_cascade(Xi, sos)` | `biquad_cascade.m` | M4 `biquad_bank` | Q3.14 系数 / Q8.15 状态 | 逐样本对比 |
| `Xout = Xf(:,:,i0:i1)` | `preprocess` | M6 窗口 | — | 窗长一致（626） |
| `Xout - mean(Xout,3)` | `preprocess` | M3 高通顺带完成 | — | — |
| `csp_train(...)` | `csp_train.m` | **PC 离线**（C1） | 输出 Q2.14 | 参数比对 |
| `W.' * Xi` | `csp_features.m` | M5 `csp_project` | Q2.14 × Q8.15 → Q10.13 | 逐值对比 |
| `sum(Zq.^2,2)` / `var(Z)` | `csp_features.m` | M6 `win_var` | 48 bit 整数 | 逐值对比 |
| `log2(r)*log(2)` | `csp_features.m` fixed | M7 `lognorm` | Q4.11 | 相对误差 < 1e−3 |
| `fitcsvm` + 折叠 | `csp_svm_validate>fit_svm` | **PC 离线**（C2） | 输出 Q4.11 / Q8.23 | 参数比对 |
| `F*w + b > 0` | `predict_labels` | M8 `svm_core` | Q4.11 × Q4.11 | 标签逐值一致 |
| `fpga_export(...)` | `fpga_export.m` | 参数文件 | — | 溢出检查通过 |

**MATLAB 已给出的基准数字（用于 PL 对拍）**

| 指标 | MATLAB 实测 | PL 目标 |
|---|---|---|
| cross 准确率（9 被试平均） | **73.98%** | 与定点仿真差 < 0.5 pp |
| within 准确率 | 72.05% | — |
| 定点 vs 浮点 | **−0.05 pp** | 复现该结论 |
| B01 cross | 61.88% | 逐试次标签一致 |
| 单试次 PL 处理延迟 | — | **< 10 µs** |

---

## 6. 交接里程碑与验收

| 阶段 | 交付方 | 内容 | 验收判据 |
|---|---|---|---|
| **H1** | PC | 参数文件 + golden vector + 测试向量生成脚本 | 参数溢出检查全过；golden vector 覆盖全部 9 被试 |
| **H2** | PL | M1+M2（UART + 解析） | T1–T3 测试通过（见 04 号文档 §9） |
| **H3** | PL | M3+M4（滤波链） | 输出与 MATLAB `biquad_cascade` **逐样本一致** |
| **H4** | PL | M5+M6（CSP + 滑动窗） | `z` 与方差逐值一致 |
| **H5** | PL | M7+M8（特征 + 判决） | 特征相对误差 < 1e−3；**标签逐试次一致** |
| **H6** | PS | 参数下发 + 状态监控 + 可视化 | 换参数后精度可复现；CRC 失败能正确拒绝 |
| **H7** | 系统 | 端到端实时 | 9 被试平均准确率 ≥ 73.5%；PL 延迟 < 10 µs；连续 30 min 无丢帧 |
| **H8** | 系统 | 决赛演示脚本 + 降级路径 | 连续 3 次彩排零失败 |

**每个阶段的硬规矩**：**只动一个模块，动完立刻跑全量回放回归测试**（MODE_REPLAY + 9 被试精度复现）。

---

## 7. 风险与对策

| # | 风险 | 后果 | 对策 |
|---|---|---|---|
| R1 | 训练端与推理端特征定义不一致 | 准确率暴跌且无报错 | 用归一化定义；H5 做逐试次标签比对 |
| R2 | `n_comp` 硬编码成 4（2b 只有 3 通道） | 越界或静默截断 | `FEAT_DIM` 做成寄存器 |
| R3 | biquad 反馈路径时序不收敛 | 上板跑不到 100 MHz | 用转置直接 II 型；必要时降 Fmax |
| R4 | 参数下发中途失败 | 用半截参数跑，精度莫名下降 | CRC 校验 + 失败拒绝 + 回读确认 |
| R5 | 换被试忘记清滤波器状态 | 前一被试瞬态污染 | 强制软复位流程 |
| R6 | PS 被绘图/写卡阻塞 | 显示卡顿（**不影响数据通路**，因解析在 PL） | 分线程 + 采集线程不碰 I/O |
| R7 | 滑动和长时间运行漂移 | 方差失真 | 整数加减本身零漂移；保留每 2 s 重算兜底 |
| R8 | 定点 log 用查表法与 MATLAB 不一致 | 特征偏差 | H5 专门验证这一级 |

---

## 附录 A：PL 顶层端口表

```verilog
module eeg_csp_svm_top (
    // 时钟与复位
    input  wire        clk,            // 100 MHz
    input  wire        rst_n,

    // Cyton 串口（见 04 号文档）
    input  wire        uart_rx,        // ← Cyton D11（经隔离器）
    output wire        uart_tx,        // → Cyton D12

    // AXI-Lite 从机（PS 控制）
    //   AW/W/B/AR/R 五通道，32 位数据，地址见 §4.1

    // 调试旁路（可选）
    output wire        dbg_we,
    output wire [15:0] dbg_addr,
    output wire [31:0] dbg_data
);

// 内部关键信号
wire        sample_valid;
wire [23:0] ch_data [0:7];
wire [7:0]  sample_seq;
wire [31:0] err_flags;

wire        dec_valid;             // 每个采样点更新
wire        label;
wire signed [31:0] decision;       // Q8.23

// 参数（经 AXI-Lite 写入）
wire signed [17:0] iir_sos [0:4][0:4];   // Q3.14
wire signed [15:0] dc_a;                 // Q1.15
wire signed [15:0] csp_w  [0:7][0:7];    // Q2.14
wire signed [15:0] svm_w  [0:7];         // Q4.11
wire signed [31:0] svm_b;                // Q8.23
wire [15:0]        win_len;
wire [7:0]         n_ch, feat_dim;
endmodule
```

## 附录 B：PS 侧参数下发伪代码

```python
import json, struct
from pynq import Overlay, allocate

ol = Overlay("eeg_csp_svm.bit")
ip = ol.eeg_csp_svm_0

P = json.load(open("params/csp_svm_params.json"))
assert P["n_ch"] == P["csp_W"] and P["feat_dim"] == P["n_comp"]

ip.register_map.CTRL = 0b0110          # soft_reset + clear_accum
time.sleep(0.01)
ip.register_map.CTRL = 0

def push_block(block_id, values, fmt):
    ip.register_map.PARAM_ADDR = block_id << 24 | len(values)
    for i, v in enumerate(values):
        ip.register_map.PARAM_RAM = v & 0xFFFFFFFF
    ip.register_map.PARAM_COMMIT = 1
    while not ip.register_map.STATUS & 0b100:      # params_crc_ok
        if ip.register_map.STATUS & 0b1000:        # crc fail
            raise RuntimeError(f"参数块 {block_id} CRC 校验失败")

push_block(0x01, [v for row in P["iir"]["sos_q"] for v in row], "i")
push_block(0x02, [P["dc_a_q1_15"]], "h")
push_block(0x03, [v for row in P["csp_W"] for v in row], "h")
push_block(0x04, P["svm"]["w"], "h")
push_block(0x05, [P["svm"]["b"]], "i")

# 回读校验
assert ip.register_map.SVM_B_Q8_23 == P["svm"]["b"], "回读不一致！"

ip.register_map.MODE = 0                # live
ip.register_map.CTRL = 1                # start

# 采集循环（高优先级线程）
while running:
    d = ip.register_map.DECISION
    label, dec = d & 1, struct.unpack("i", struct.pack("I", d & 0xFFFFFFFE))[0] / 2**23
    queue.put((time.perf_counter(), label, dec))
```

---

## 附录 C：一页速查

```
PL（实时路径，~10% 资源，延迟 ~3 µs）
  UART → 解析 → 去DC → Cheby2 5节 → CSP投影 → 滑动窗 → 归一化log → 线性SVM → 标签
         ↑                                  ↑
      0x41包                             W 可 AXI-Lite 重载

PS（控制与交互）
  参数下发(CRC+回读) · 状态监控 · 结果读取 · 可视化 · 记录 · 范式呈现(cue)
  [进阶] 协方差累加 → Jacobi EVD → 新 W 下发（在线自适应）
  [进阶] RBF 核 SVM（对比实验，不经 PL）

PC（离线训练，已完成）
  CSP 训练(EVD) → SVM 训练 → 折叠 → 定点量化 → 参数头文件 + golden vector

验收基线（MATLAB 实测，PL 必须复现）
  cross 73.98% | within 72.05% | 定点 vs 浮点 −0.05 pp | PL 延迟 < 10 µs
```

---

*本文档定义的任务分配与接口，与 [02 号算法规格书] 的定点格式、[04 号串口接入文档] 的链路格式保持一致。
任何一侧的改动必须三方同步并跑全量回归。*
