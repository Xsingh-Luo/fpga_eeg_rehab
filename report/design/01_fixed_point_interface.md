# 01 · 定点格式与接口约定（工程契约）

> 配套文档：`00_总体实施规划.md`
> 本文档定义 **PL / PS / PC 三端必须共同遵守的接口与数值约定**。
> **一旦冻结，任何一方改格式都必须三方同步 + 跑全量回归。**
> 版本：v1.0（草案，待 P1 位宽扫描后冻结）

---

## 1. 全局定标约定（最重要的一条）

### 1.1 数值域：直接用 ADC 整数计数域，不做 μV 转换

特征为对数归一化方差：

$$f_i = \log\frac{\mathrm{var}(z_i)}{\sum_j \mathrm{var}(z_j)}$$

对信号整体缩放 $a$ 倍**完全免疫**。因此：

```
✅ 正确：PL 内部一律用 ADC 整数计数（int24）的定标域
❌ 错误：在 PL 里乘 0.02235 转成 μV（纯浪费 DSP，且引入浮点）
```

> ⚠️ **前提**：训练端（PC）算 CSP 和训练 SVM 时，**必须用同一份未转 μV 的原始计数数据**，或至少保证训练用的 `W` 与部署时 `x` 的定标一致。如果训练用 μV、部署用计数，`W` 的数值会差 4.5 万倍——定点下必定溢出。

### 1.2 全局缩放因子 `G`

若训练端不可避免地用了不同定标，用**唯一的**全局因子 `G` 换算：

```
x_pl = round(x_train * G)
```

`G` 写入参数文件头部，PL 加载参数时校验，PS 显示时用它还原。

---

## 2. 各节点定点格式表

命名规范：`<signal>_Q<m>_<n>_w<总位宽>`，如 `x_Q8_15_w24`。

| # | 节点 | 符号 | 格式 | 位宽 | 范围 / 备注 |
|---|---|---|---|---|---|
| 1 | ADC 原始 | `adc` | int24 有符号 | 24 | ADS1299 计数，含百 mV 级 DC 偏移 |
| 2 | DC 去除后 | `x_dc` | Q4.19 | 24 | 动态范围收到 ±100 μV 量级 |
| 3 | 带通 IIR 系数 | `sos` | **Q3.14** | **18** | Cheby2 10 阶 = 5 个二阶段 × {b0,b1,b2,a1,a2}；16 bit 下 b1≈−1.99 余量不足 |
| 4 | IIR 累加器/状态 | `acc_iir` | 整数 | **48** | 转置直接 II 型；状态与累加器统一 48 bit 饱和 |
| 5 | 带通输出 | `x` | **Q8.15** | 24 | 进 CSP 的信号，留足 SNR 余量 |
| 6 | DC 去除滤波器状态 | `s_dc` | Q4.19 | 24 | 一阶 IIR 内部状态 |
| 7 | CSP 权重 | `W` | **Q3.14** | **18** | ★ EA 折叠后量级升到 ~3.8，Q2.14(16bit) 会溢出。每带 N×n_comp 个 |
| 8 | CSP 投影 | `z` | Q10.13 | 24 | `z = Wᵀx` |
| 9 | z 运行和 | `Sz` | 整数 | **40** | 窗内 ≤ 1024 点 |
| 10 | z² 运行和 | `Sz2` | 整数 | **48** | 定点整数加减 → **理论零漂移** |
| 11 | 方差 | `var` | Q20.27 | 48 | `var = (Sz2 − Sz²/T) / T` |
| 12 | 方差和 | `S` | Q20.27 | 48 | `Σ var_i`，i = 1..2m |
| 13 | 倒数 | `inv_S` | Q2.29 | 32 | Newton-Raphson 2 次迭代 |
| 14 | 归一化比值 | `r` | Q1.30 | 32 | `r_i = var_i · inv_S`，Σr = 1 |
| 15 | log2 | `l2` | Q8.23 | 32 | `log2(r)`，r ∈ (0,1] → l2 ≤ 0 |
| 16 | **特征** | `f` | **Q4.11** | 16 | `f_i = l2_i · ln2`，自然对数域，范围约 [−4, 0] |
| 17 | SVM 支持向量 | `SV` | Q4.11 | 16 | K×2m 个，**必须与 `f` 同域** |
| 18 | SVM 对偶系数 | `alpha` | Q1.14 | 16 | K 个 |
| 19 | 偏置 | `b` | Q8.23 | 32 | |
| 20 | 核宽度 | `gamma` | Q6.9 | 16 | **必须用定点特征重新标定**，不可直接搬浮点值 |
| 21 | RBF 距离平方 | `d2` | Q8.23 | 32 | `‖f − sv‖²` |
| 22 | 决策值 | `dec` | Q8.23 | 32 | `Σ α_k·K(f,sv_k) + b`；符号即标签 |

### 2.1 定点实现细则

| 规则 | 说明 |
|---|---|
| **舍入** | 统一 **round-half-up**，不用截断（截断在累加链上会累积负偏） |
| **饱和** | 所有中间结果**必须饱和**而非回绕（回绕会让误差爆炸且难定位） |
| **溢出保护** | 每个加法器留 ≥ 2 bit 保护位；综合后用 `csynth` 的位宽报告核对 |
| **Σz² 累加** | 全整数加/减 → **无漂移**。但仍保留"每 2 s 从零重算一次"的兜底逻辑（可配开关） |
| **log 实现** | `log2(x)`：前导零计数得整数部分 + 尾数 512 点表 + 线性插值；最后乘 `ln2`（Q1.15） |
| **exp 实现（RBF）** | 输入域 `-γ·d2 ∈ [−20, 0]`；128 段线性插值 LUT，最大相对误差 < 1e-3 |
| **倒数** | Newton-Raphson：`y ← y(2 − S·y)`，初值查表；2 次迭代足够 |

---

## 3. 串口帧与 PL 输入接口

### 3.1 OpenBCI Cyton 串口包（33 字节）

| 偏移 | 长度 | 字段 | 处理 |
|---|---|---|---|
| 0 | 1 | `0xA0` 帧头 | 校验，失败则丢弃并计数 |
| 1 | 1 | 采样序号（0–255 回绕） | **用于丢包检测和 trigger 对齐** |
| 2 | 24 | 8 通道 × 3 字节（**大端、有符号 24 bit**） | 主数据 |
| 26 | 6 | AUX 字节 | 含义由帧尾字节决定：加速度计 / 原始 AUX / 用户自定义 / 带时间戳 |
| 32 | 1 | 帧尾 `0xCX`（X = 0–F） | 校验，并决定 AUX 字节含义 |

- 打包速率：33 B × 250 Hz = **8250 B/s** → 115200 8N1（11520 B/s 容量）下占用 **71.6%**
- 采样率：250 Hz → 每 4 ms 一帧
- **丢包判定**：`seq[n+1] − seq[n] != 1 (mod 256)` → 丢包计数 +1，并**在数据流中插入零填充**以保持时间轴对齐（不要让 PL 看到"跳变的时间轴"）
  - ⚠️ 序号只有 1 字节，**丢包超过 255 个样本时无法区分**；OpenBCI 官方口径是丢包 > 1% 这份记录就不可用
  - Cyton 数据流**没有流控**，以固定 250 包/秒推送，主机必须及时取走

> 注：帧头是 `0xA0` 不是 `0xAA`，帧尾是 `0xCX` 不是固定字节——这两点很多二手资料写错，以 OpenBCI 官方 Data Format 文档为准。

### 3.2 PS → PL 的 AXI4-Stream 帧格式

```
每帧（一拍 24 bit，TDATA 宽度 24，TKEEP 全 1）：
   tdata[23:0] = x[ch]        ch = 0..N-1 顺序输出
   tuser[0]    = frame_start   （该帧第 0 通道时置 1）
   tuser[1]    = trigger       （该采样点为 cue 时刻）
   tuser[2]    = pad_flag      （该帧为丢包补零）
   tlast       = 第 N-1 通道时置 1
```

- **不要逐样本 DMA**。PS 每 20~50 ms（5~13 帧 × N 通道）打包一次，用 AXI-DMA 的 `S2MM` 通道突发搬运，中断摊销。
- PL 内部 `frame_parser` 把流还原为 `N` 个通道的并行数据。

---

## 4. AXI-Lite 寄存器映射（完整草案）

基地址：`0x43C0_0000`（示例，以 Vivado Address Editor 为准）

| 偏移 | 名称 | R/W | 位域 | 说明 |
|---|---|---|---|---|
| `0x000` | `CTRL` | RW | [0] start<br>[1] soft_reset<br>[2] clear_accum<br>[3] dbg_en<br>[4] zero_recalc_en | 软复位必须清空所有滤波器与累加器状态 |
| `0x004` | `STATUS` | RO | [0] busy<br>[1] params_loaded<br>[2] params_crc_ok<br>[7:3] reserved<br>[15:8] drop_cnt<br>[23:16] seq_err_cnt | 丢包计数实时可读 |
| `0x008` | `MODE` | RW | [1:0] 0=live 1=replay 2=inject 3=reserved | **决赛降级开关** |
| `0x00C` | `WIN_LEN` | RW | [15:0] 窗口点数 T（默认 500 = 2 s @250 Hz） | 改动后自动清累加器 |
| `0x010` | `N_CH` | RW | [7:0] 通道数（3/8/16/32） | 用于通道扩展实验 |
| `0x014` | `DECISION` | RO | [0] label (0/1)<br>[31:1] 决策值 Q8.23 | 每采样点更新 |
| `0x018` | `SVM_CFG` | RW | [0] kernel (0=linear,1=RBF)<br>[15:1] K（SV 个数）<br>[31:16] reserved | 切核需同步重载参数 |
| `0x01C` | `GAMMA` | RW | [15:0] γ（Q6.9） | 仅 RBF 有效 |
| `0x020` | `PERF_CNT` | RO | PL 处理周期数（上一个决策） | **实测延迟的证据** |
| `0x024` | `ACC_CNT` | RO | 累计决策数 | 与 PS 收包数交叉验证 |
| `0x100–` | `PARAM_RAM` | WO | 参数写入窗口 | 见 §5.3 |

**中断**：`DECISION` 更新时可选择产生中断（默认**关闭**，改为 PS 轮询或 DMA 批量读，避免中断开销）。

---

## 5. 参数文件格式

### 5.1 唯一的真实来源：JSON（训练端产出）

```jsonc
{
  "version": "2026-10-15T10:30:00Z",
  "dataset": "BCI_IV_2b",
  "subject": "B01",
  "global_scale_G": 1.0,          // §1.2 全局定标
  "fs": 250,
  "n_ch": 3,                       // 2b = 3；自采 OpenBCI = 8
  "win_len": 626,                  // [3.5, 6.0] s @250 Hz
  "n_band": 3,                     // ★ 8-16 / 16-24 / 24-32 Hz
  "bands": [[8,16],[16,24],[24,32]],
  "m": 1,                          // 每侧 CSP 对数
  "n_comp": 2,                     // ★ 每带实际 = min(2m, n_ch)，2b 下 m=1 → 2
  "n_feat_all": 6,                 // ★ n_band × n_comp
  "feat_dim": 4,                   // ★ MIBIF 选中的维数，不是 n_feat_all
  "sel_idx": [0, 1, 4, 5],         // ★ MIBIF 索引（0 基），PL 只算这几路
  "feature": "logvar_norm",        // ★ 归一化对数方差，不是 MNE 的 log(var)
  "tmin": 3.5, "tmax": 6.0,
  "causal": true,                  // ★ FPGA 只能因果；零相位仅供离线
  "ea": { "n_cal": 20, "folded": true },   // 白化已折进 csp.W
  "q_formats": {
    "x":      {"m": 8,  "n": 15, "w": 24},
    "sos":    {"m": 3,  "n": 14, "w": 18},
    "W":      {"m": 3,  "n": 14, "w": 18},   // ★ EA 折叠后需 18 bit
    "z":      {"m": 10, "n": 13, "w": 24},
    "feature":{"m": 4,  "n": 11, "w": 16},
    "w_svm":  {"m": 4,  "n": 11, "w": 16},
    "b":      {"m": 8,  "n": 23, "w": 32}
  },
  "dc": { "a": 32362 },            // 一阶高通 0.5 Hz，Q1.15
  "iir": {
    "type": "butter", "order": 4, "sections_per_band": 2,
    "sos_q3_14": [                 // [带][节]，每节 {b0,b1,b2,a1,a2}
      [ [  ..., ..., ..., ..., ... ], [  ..., ..., ..., ..., ... ] ],   // 8-16
      [ [  ..., ..., ..., ..., ... ], [  ..., ..., ..., ..., ... ] ],   // 16-24
      [ [  ..., ..., ..., ..., ... ], [  ..., ..., ..., ..., ... ] ]    // 24-32
    ]
  },
  "csp": { "W": [ /* n_band 个矩阵，每个 n_ch × n_comp 个整数，行优先。★ 已含 EA 折叠 */ ] },
  "svm": {
    "kernel": "linear",            // ★ 已折叠，不需要存 SV
    "w": [ /* feat_dim 个整数，Q4.11 */ ],
    "b": -56789                    // Q8.23 整数
  },
  "crc32": "0xDEADBEEF"            // 对除本字段外的全部内容计算
}
```

> **★ 线性核折叠**：`w = dual_coef_ @ support_vectors_`，实测与原始决策函数的最大差 1.8e−13、预测标签完全一致。
>
> **★ EA 折叠**：`W_eff = M_bᵀ · W̃_b`（每带独立）。**下发的是 W_eff**，PL 侧完全不感知 EA 的存在。
> B01 的线性 SVM 有 **261 个支持向量**，但折叠后 PL 只需存 **3 个权重 + 1 个偏置** —— BRAM 占用降两个数量级。详见 [02 号文档](02_algorithm_spec.md) §2.6。

### 5.2 生成物：C 头文件（PS 与 PL 测试平台共用）

```c
/* AUTO-GENERATED by python/export_fixed.py — DO NOT EDIT */
#ifndef CSP_SVM_PARAMS_H
#define CSP_SVM_PARAMS_H
#define PARAM_VERSION   "2026-10-15T10:30:00Z"
#define PARAM_CRC32     0xDEADBEEFu
#define N_CH            3          /* 2b=3；自采 OpenBCI=8 */
#define WIN_LEN         626        /* [3.5,6.0] s @250 Hz */
#define N_BAND          3          /* ★ 8-16 / 16-24 / 24-32 Hz */
#define N_COMP          2          /* ★ 每带 = min(2m, N)，m=1 时是 2 */
#define N_FEAT_ALL      6          /* ★ = N_BAND × N_COMP */
#define FEAT_DIM        4          /* ★ MIBIF 选中数，不是 N_FEAT_ALL */
#define IIR_SEC_PER_BAND 2         /* 每带 4 阶巴特沃斯 = 2 个二阶段 */
#define SVM_KERNEL_LINEAR 1        /* 线性核已折叠 */
#define IIR_COEF_Q      14
#define IIR_COEF_W      18         /* Q3.14，16 bit 余量不足 */

extern const int32_t iir_sos_q3_14[N_BAND][IIR_SEC_PER_BAND][5];  /* [带][节]{b0,b1,b2,a1,a2} */
extern const int16_t dc_a_q1_15;                                  /* 一阶高通系数 */
extern const int16_t csp_W_q2_14[N_BAND][N_CH * N_COMP];          /* ★ 已含 EA 折叠，行优先 */
extern const uint8_t mibif_sel_idx[FEAT_DIM];                     /* ★ 从 6 维中选哪 4 维 */
extern const int16_t svm_w_q4_11[FEAT_DIM];                       /* ★ 折叠后的权重 */
extern const int32_t svm_b_q8_23;
#endif
```

> **★ 三个"不是想当然"的维度**：
> `N_COMP` 是 `min(2m, N)` 而不是 `2m`；`N_FEAT_ALL` 是 `N_BAND × N_COMP`；
> `FEAT_DIM` 是 MIBIF 选中的数量（4），**既不等于 `N_FEAT_ALL`(6) 也不等于 `N_CH`(3)**。
> 这三个都必须是可配的宏/寄存器，硬编码必错。

### 5.3 PL 参数加载协议

1. PS 写 `PARAM_RAM` 起始地址 + 长度 + 目标块 ID（`SOS` / `W` / `SV` / `α` / 其他）。
2. PL 把参数写入对应 BRAM。
3. PS 写 `PARAM_COMMIT` → PL 计算 CRC32 与自己的校验逻辑比对 → 置 `STATUS.params_crc_ok`。
4. `params_crc_ok == 0` 时 PL **拒绝使用新参数**，继续用旧参数并置错误位。

> **参数加载失败必须可见。** 静默使用半截参数会导致"精度莫名其妙掉了"这种最难查的 bug。

---

## 6. Golden Vector 文件格式（对拍用）

```jsonc
{
  "param_version": "2026-10-15T10:30:00Z",
  "source": "python_fixed_point_sim",   // 或 ps_c_ref
  "cases": [
    {
      "id": "B01_sess2_trial017",
      "input":  { "path": "golden/in/B01_s2_t017.bin", "dtype": "int24", "shape": [8, 500] },
      "stages": {
        "x_bp":   { "path": "golden/ref/B01_s2_t017_xbp.bin",   "dtype": "int24", "q": "Q8.15" },
        "z":      { "path": "golden/ref/B01_s2_t017_z.bin",     "dtype": "int24", "q": "Q10.13" },
        "feat":   { "path": "golden/ref/B01_s2_t017_feat.bin",  "dtype": "int16", "q": "Q4.11" },
        "dec":    { "path": "golden/ref/B01_s2_t017_dec.bin",   "dtype": "int32", "q": "Q8.23" },
        "label":  1
      }
    }
  ]
}
```

**回归判定标准（硬性）**

| 级别 | 判据 | 不满足时的动作 |
|---|---|---|
| **PL vs PS-C** | 逐 bit 完全一致 | **视为 bug，必须修**，不允许"精度差不多" |
| **Python 定点 vs PL** | 逐 bit 完全一致 | 同上 |
| **Python 定点 vs Python 浮点** | 特征相对误差 < 1e-3，准确率差 < 0.5% | 调位宽 / 查定标 |

---

## 7. 目录与命名规范

```
FPGA2026QS/
├─ 规划/                    规划与文档
│   ├─ 00_总体实施规划.md
│   ├─ 01_fixed_point_interface.md
│   ├─ figures/             框图
│   └─ tools/               文档生成脚本
├─ python/                  PC 端
│   ├─ baseline.py          浮点基线
│   ├─ fixed_point_sim.py   定点仿真
│   ├─ train_csp_svm.py     训练
│   └─ export_fixed.py      参数导出（唯一真实来源）
├─ params/                  导出的参数（JSON + .h + .mem）
├─ golden/                  对拍向量
│   ├─ in/                  输入
│   └─ ref/                 各级参考输出
├─ hls/                     Vitis HLS 工程
├─ rtl/                     手写 RTL
├─ vivado/                  Block Design 与约束
└─ ps/                      PS 端（PYNQ notebook / C 程序）
```

**命名**：`<模块>_<功能>_<版本>`，版本用日期 + 小写字母，如 `csp_top_v20261015a`。

---

## 8. 冻结检查表（P1 结束前必须逐项勾选）

- [ ] `G` 全局定标因子确定，训练/推理两端一致
- [ ] 位宽扫描完成，§2 表格中所有位宽标为"已冻结"
- [ ] IIR 系数量化后，通带纹波与阻带衰减仍满足指标（画频响对比图）\n- [ ] 各节通带峰值增益差 37 倍（0.099~3.70），已确认状态位宽按最坏节点留够余量
- [ ] `gamma` 用**定点特征**重新标定过（不是搬浮点值）
- [ ] 定点 vs 浮点：特征相对误差 < 1e-3 ✔，准确率差 < 0.5% ✔
- [ ] golden vector 生成完毕，覆盖全部 9 个被试（若用 2b）
- [ ] 参数 JSON schema 与导出脚本联调通过
- [ ] CRC32 校验逻辑在 PS 侧与 PL 侧实现一致

---

*本文档中的位宽为**起始建议值**，必须经 P1 阶段的位宽扫描实验确认后冻结。修订请同步更新 `00_总体实施规划.md` 的附录 A。*
