# MATLAB 验证：3 子带 FBCSP + 欧氏对齐 + 线性 SVM

用 BCI Competition IV 2b 开源数据，在本目录独立复现并验证
[算法实现规格书](../../report/design/02_algorithm_spec.md) 描述的完整链路。

**本目录是权威实现** —— 与 FPGA 逐级对拍的基准。Python 侧（`../python/`）只用于快速实验，
任何结论定稿前都要在这里复核。

---

## 怎么跑

```powershell
matlab -batch "cd('H:\FPGA2026QS\matlab'); train_final"          # ★ 主流程：训练→折叠→导出→定点校验
matlab -batch "cd('H:\FPGA2026QS\matlab'); ea_calib_validate2"   # 真实部署口径（session 内校准）
matlab -batch "cd('H:\FPGA2026QS\matlab'); fbcsp_ea_validate"    # 配置对比 + 校准量扫描
matlab -batch "cd('H:\FPGA2026QS\matlab'); csp_svm_validate"     # 单带 CSP 基线（对照）
```

**依赖**：MATLAB R2023b + Statistics and Machine Learning Toolbox（`fitcsvm`）。
**不依赖** Signal Processing Toolbox —— 所有滤波都是手写的（理由见下）。

---

## 当前结果（真实部署口径）

**配置**：3 子带 FBCSP（8-16 / 16-24 / 24-32 Hz）+ 欧氏对齐（**session 内** 20 试次校准）+ 归一化对数方差 + 线性 SVM

| 被试 | 浮点 | 定点 | kappa |
|---|---|---|---|
| B01 | 75.36% | 75.36% | +0.507 |
| B02 | 55.42% | 55.42% | +0.108 |
| B03 | 59.29% | 59.29% | +0.186 |
| **B04** | **95.71%** | 95.71% | **+0.914** |
| **B05** | **95.36%** | 95.36% | **+0.907** |
| B06 | 86.07% | 86.07% | +0.721 |
| B07 | 77.86% | 78.21% | +0.557 |
| B08 | 91.79% | 91.79% | +0.836 |
| B09 | 86.07% | 86.07% | +0.721 |
| **平均** | **80.32%** | **80.36%** | **+0.606** |

**2008 官方冠军 kappa +0.600 —— 已超过。**

**六条结论**

1. **欧氏对齐（EA）有效**：session 内校准下 **+2.94 pp**（N=20）。
2. **★ EA 的校准必须与正式任务在同一 session**。跨 session 校准（如把 04E+05E 拼起来取前 N 个）
   增益会消失甚至为负——这是实验设计错误，不是方法失效。**这是本轮踩过的最大的坑。**
3. **校准 20 个试次最优**（约 1 分钟）。10 个可用但不稳，80 个反而退化（session 内漂移）。
4. **3 带优于 4/6/9 带**：79.40 > 79.77 > 79.03 > 78.47（含 EA）。
   窄带（4 Hz）群延迟长、噪声多；宽带（8 Hz）更好**且 PL 资源省一半**。
5. **定点几乎无损**：+0.04 pp。
6. **被试间差异极大**：B04 95.7% vs B02 55.4%。现场演示必须**先筛人**。

---

## 文件说明

| 文件 | 作用 |
|---|---|
| **`train_final.m`** | **主流程**：训练 → EA 折叠 → 导出参数 → 定点校验 → golden vector |
| **`ea_calib_validate2.m`** | 真实部署口径验证（session 内校准，有/无 EA 严格对照） |
| `fbcsp_ea_validate.m` | 配置对比（校准量扫描、因果 vs 零相位） |
| `euclid_align.m` | 欧氏对齐：`R̄ → M = R̄^(-1/2)`，支持指定校准试次数 |
| `prep_bands.m` | 去DC → 逐带带通 → 裁剪 → 去均值（多子带） |
| `fbcsp_eval.m` | 多子带 CSP + MIBIF + SVM 评估（三道防泄漏） |
| `csp_train.m` | CSP 空间滤波器训练，**只在 PC 上做** |
| `csp_features.m` | 归一化对数方差特征，支持浮点/定点两种模式 |
| `mibif_select.m` | 互信息特征选择（手写直方图法，不依赖工具箱） |
| `biquad_cascade.m` | 手写二阶段级联滤波（转置直接 II 型），浮点与定点逐位模拟 |
| `fpga_export.m` | 量化 + 溢出检查 + 生成 C 头文件与 JSON |
| `nbpw_predict.m` | NBPW 分类器（**仅对照实验**，实测与线性 SVM 打平，未采用） |
| `csp_svm_validate.m` | 单带 CSP 基线 + 两种协议 + 定点对比（对照用） |
| `cheby2_bandpass_sos.m` | 单带 Cheby2 10 阶系数（早期方案，对照用） |
| `data/fbcsp_filters.mat` | **3 子带 SOS 系数**（由 `python/export_fbcsp_filters.py` 生成） |
| `data/subj01.mat … subj09.mat` | 数据（由 `python/export_2b_to_mat.py` 生成） |

**产物**：`../params/csp_svm_params.h` / `.json` / `golden_vectors.mat`；`data/final_train.mat`。

---

## 六个刻意的实现选择

### ① 手写 biquad，不用 `filter`/`sosfilt`

`biquad_cascade.m` 用**转置直接 II 型**逐样本递推，与 PL 实现**逐行对应**：

```
y[n] = b0*x[n] + s1
s1   = b1*x[n] - a1*y[n] + s2
s2   = b2*x[n] - a2*y[n]
```

好处：① 不依赖 Signal Processing Toolbox；② `mode='fixed'` 时逐位模拟硬件的整数运算
（系数 Q14、状态 Q15、累加器 48 bit 饱和），**这才是真正能验证定点方案的东西**。

### ② 数据用 int16 计数域，不用 µV 浮点

`export_2b_to_mat.py` 把 µV 数据按 `G = 294.89 counts/µV` 量化成 int16，
与 FPGA 的 ADC 输入域一致。这样 MATLAB 验证的就是**和硬件同域的整数运算**。

### ③ 特征必须用归一化定义

`csp_features.m` 算的是 `f_i = log(var(z_i) / Σ_j var(z_j))`，
**不是** MNE 默认的 `log(var(z_i))`。

实测（把输入整体放大 100 倍模拟增益标定差 40 dB）：
```
MNE log(var)          特征平移 9.210   ← 完全跑偏
归一化 log(var/Σvar)  特征变化 4e-16   ← 恒定不变
```
归一化让 PL 端不需要精确增益标定，且对通道间增益差异免疫。

### ④ CSP 投影必须是 `z = Wᵀx`

`csp_features.m` 里是 `Z = W.' * Xi`，即 $W^Tx$ —— `W` 的**列**是空间滤波器。

> ⚠️ **写成 `W * Xi` 会用错矩阵**（用成 pattern 矩阵而非 filter 矩阵）。
> 3 通道时 `W` 是 3×3 方阵，**这个错误不会报错，结果也"看着合理"**。
> Python 侧曾因此静默偏差数周，直到测 `m=1` 变成 3×2 非方阵才崩溃。
> **任何矩阵方向约定都要加数值自检。**

### ⑤ ★ EA 折叠后必须喂**未对齐**数据

```matlab
Weff = Mb.' * W;                       % 折叠
acc  = eval(Bs_raw, Weff);             % ★ 用原始未对齐数据
% acc = eval(Bs_aligned, Weff);        % ✗ 双重对齐，掉 5.8 pp 且不报错
```

因为 $W_{\text{eff}}^Tx = (M^TW)^Tx = W^TMx = W^Tx_{\text{aligned}}$。

### ⑥ ★ CSP 权重格式升到 Q3.14 / 18 bit

EA 折叠放大动态范围：$W_{\text{eff}}$ 实测最大 **3.81**（未折叠时 < 2.0）。
Q2.14 / 16 bit 的范围是 ±2.0 → **溢出**。已改为 Q3.14 / 18 bit，余量 63~75%。

---

## 已知差异与后续

| 项 | 说明 |
|---|---|
| 与 Python 差约 0.4 pp | 互信息估计方法（直方图 vs sklearn kNN）与 CSP 正则化细节，属预期 |
| 正则化用 Ledoit-Wolf | 与 Python 一致；`cfg.reg='fixed'` 可切到 α=0.05 |
| 因果 vs 零相位 | 差约 0.9 pp；**FPGA 只能因果**，`cfg.causal` 控制 |
| 定点 log 用 `log2` 直接算 | 未模拟 512 点 LUT + 线性插值；上板前需在 HLS 侧单独验证这一级 |
| **未覆盖** | 位宽扫描（研究空白点②）、黎曼切空间对比、EEGNet 离线对比 |

**下一步**：用 `params/golden_vectors.mat` 逐级对拍（滤波后信号 / z / var / f / 决策值），
这是规格书 §5 与 05 号文档 H3–H5 要求的验收基准。

---

## 新记录离线推理：`predict_record.m`（2026-10-31 新增）

把"训练好的模型"用到**新采的一段连续记录**上（例如 RTEEGAP 保存的 `RecordData`）。
输入按 `record/1.mat` 的格式：`RecordData.NowData` = 8×N（µV，250 Hz）。

```matlab
cd('H:\FPGA2026QS\matlab')

predict_record                                       % 默认读 ..\record\1.mat
predict_record('H:\FPGA2026QS\record\1.mat')        % 指定文件
load('H:\FPGA2026QS\record\1.mat'); predict_record(RecordData)   % 直接传工作区变量
out = predict_record(..., 'strideSec',0.25, 'eaCalStartSec',5.0);   % 返回结果结构体
```

**做了什么**

1. 读 `RecordData.NowData`，取 **CH1–CH3 = C3/Cz/C4**（模型 N_CH=3，CH4–CH8 不参与）；
2. 与 `prep_bands.m` 同口径：0.5 Hz 去 DC → 3 子带 8-16/16-24/24-32 Hz → 每窗去均值；
3. 2.5 s 分析窗（626 点，对应 2b 的 [3.5,6.0] s）/ 可选滑窗步长；记录没有事件标记，
   因此逐窗解码后投票，而不是按试次；
4. 两种口径：
   - **baked**：直接用 `golden_vectors.mat` 里的折叠参数 `W_eff`（= 把这段数据回放给
     当前 FPGA 位流时 PL 实际使用的参数）；
   - **recalib**：按项目部署口径，用本段记录估计欧氏对齐矩阵 `M_new`，再折叠
     `W_eff_new = M_new' * W`。未折叠的 `W` 由导出的 `W_eff` 反解（先撤销 B04 05E 的校准矩阵），
     该链路已由 `validate_pipeline.m` 用 2b 数据验证；
5. 输出逐窗 CSV / 结果 MAT / 决策曲线 PNG / UTF-8 中文报告到 `../sessions/predict_record/`。

**先跑校验**（确认推理链路与 `train_final.m` 一致）：

```matlab
matlab -batch "cd('H:\FPGA2026QS\matlab'); validate_pipeline"
% S2(05E) 准确率 93.57% = train_log.txt 记录值；z 与 golden_vectors.mat 逐点一致；打印 PASS
```

**在 `record/1.mat` 上的结果**（8×9375，37.5 s@250 Hz，无事件标记）：

| 口径 | 全部滑窗 | 独立窗（校准后） | 平均决策值 d |
|---|---|---|---|
| baked（导出参数直用） | 70/70 右手 | 12/12 右手 | +3.6（|d|max 40.5，远超出 2b 的 1–3，域失配） |
| recalib（同 session EA 校准） | 29/70 右手 | 3/7 右手（42.9%） | −0.61 |

结论：**这段记录给不出可靠的左右手判定**（recalib 接近随机；baked 的标签是域失配造成的）。
原因见报告里的"使用限制"：没有 cue 事件、模型是 B04 单被试、记录通道共模强。

> ⚠️ **record/1.mat 的通道映射（2026-10-31 补记）**：这次录制用的是 OpenBCI 官方头套默认接线
> （官方 10-20 默认：CH1=Fp1, CH2=Fp2, CH3=C3, CH4=C4, CH5=P7, CH6=P8, CH7=O1, CH8=O2），
> 所以模型取的 CH1–CH3 实际是 **Fp1/Fp2/C3**（缺 C4），与训练用的 C3/Cz/C4 不符 ——
> 这是本段记录识别接近随机的直接原因（AUC≈0.5，前段左手/后段右手不可分）。
> 自采时请按 [规划/14 §2.1](../../report/design/14_openbci_acquisition.md) 把
> **CH1=C3、CH2=Cz、CH3=C4** 接好（Ultracortex 的 C3/Cz/C4 节点都有，挪电极+换通道线即可）。
> 若已知真实标签的时间块，可直接评估：
> `predict_record('..\record\1.mat','blocks',[0 18.75; 18.75 37.5],'blocklabels',[0 1])`。

### 无法改电极时的方案：2 通道 C3/C4 模型（`train_c3c4_2ch.m`，2026-10-31 新增）

OpenBCI 默认头套不能动电极时，它仍提供 **CH3=C3、CH4=C4** 两路关键电极（只是没有 Cz）。
`train_c3c4_2ch.m` 用 BCI IV 2b B04 训练了一个**只有 C3/C4 两通道**的 3 带 FBCSP+EA+SVM：

- 2b 复算：S1 97.86%，S2 92.86%，平均 **95.36%**（3 通道版本 95.71%，几乎无损）；
- 模型存到 `../sessions/predict_record/model_c3c4_2ch.mat`；
- 套到 `record/1.mat`（取 CH3/CH4，用本段记录做 EA 校准）：前段左手 71.4%、后段右手 25.7%、
  AUC 0.584 —— 方向对了一部分，但这 37.5 s 记录太短且开头有强瞬态，**还不能作为可用的识别**。

要用起来：用默认头套按 `sim/host_tools/openbci_record.py --paradigm mi2` 采 60 trial（带 events），
再用 `train_c3c4_2ch.m` 的同样流程在**本人数据**上重训 + 同 session 校准，就会得到真正可用的准确率。

正式评测请用 `sim/host_tools/openbci_record.py`（`--paradigm mi2`）或测试台带事件采 60 trial，
`events.csv` 会给出 cue 的采样序号，届时可按试次切窗再推理。
