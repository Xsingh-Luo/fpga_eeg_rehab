# 06 · FBCSP 与优化方法说明

> 本文档只讲**方法原理**，不含实测数值。
> **所有数字以 [07 验证结果与最终配置](07_verification_and_config.md) 为准。**
> 代码：`matlab/`（权威实现）· `python/`（快速实验）
> 版本 v2.0

---

## 0. 那个"冠军"是谁

**BCI Competition IV（2008）Dataset 2b 的官方第一名。**

| 项 | 内容 |
|---|---|
| 赛事 | BCI Competition IV（第四届国际脑机接口竞赛），2008 |
| 数据集 | **Dataset 2b**：9 被试、3 通道（C3/Cz/C4）、250 Hz、左右手二分类 |
| 冠军 | **Zheng Yang Chin**，合作者 Kai Keng Ang、Chuanchu Wang、Cuntai Guan、Haihong Zhang、Kok Soon Phua、Brahim Hamadicharef、Keng Peng Tee |
| 单位 | **新加坡资讯通信研究院（I2R, A\*STAR）** |
| 方法 | **FBCSP + Naive Bayes Parzen Window** |
| 成绩 | **kappa 0.60**（9 被试平均），约合准确率 80% |
| 出处 | [bbci.de/competition/iv/results](https://bbci.de/competition/iv/results/) |

> 该组**同时拿下了 2a 和 2b 两个数据集的冠军**，用的都是 FBCSP 家族方法——不是运气，是方法本身强。
> 同组 Kai Keng Ang 等人另发表 FBCSP 原始论文（Front. Neurosci. 6:39, 2012）。

---

## 1. FBCSP = 滤波器组 + 每个子带独立做 CSP

**动机**：ERD 出现的频段因人而异。单个 8–30 Hz 宽带滤波器把不同频段的信息糊在一起了。

```
原始信号 ──┬─► 子带 1 ──► CSP ──► 归一化对数方差 ──┐
           ├─► 子带 2 ──► CSP ──► 归一化对数方差 ──┼─► MIBIF ─► 分类器
           └─► 子带 n ──► CSP ──► 归一化对数方差 ──┘   特征选择
```

**子带数的选择很关键**（实测结论见 07 号文档）：
- 经典 FBCSP 用 9 个 4 Hz 宽的子带（4–40 Hz）
- **但子带越窄，滤波器群延迟越长、噪声越多**
- 实测 **3 个 8 Hz 宽的子带（8–16 / 16–24 / 24–32 Hz）最优**，而且 PL 资源最省

**滤波器阶数**：4 阶巴特沃斯（2 个二阶段/带）就够。
因为 FBCSP 相邻子带**故意重叠**，不需要陡峭截止——这点对 FPGA 资源至关重要。

---

## 2. MIBIF 特征选择

**Mutual Information Best Individual Feature**：算每个特征与标签的互信息，取前 k 个。

$$\text{MI}(f_i;y) = \sum_{b}\sum_{c} p(b,c)\log_2\frac{p(b,c)}{p(b)p(c)}$$

连续特征用分位分箱离散化后估计。

**★ 两条硬约束**：
1. **必须在训练折内做**——在全量数据上选完再交叉验证会信息泄漏，精度虚高
2. **选择结果只是索引表**——PL 端只保留被选中的那几路，**反而省资源**

> 实现细节会影响结果：直方图法与 sklearn 的 kNN 法（`mutual_info_classif`）实测差约 0.9 pp。
> 训练端与推理端必须用同一套。

---

## 3. NBPW 分类器

朴素贝叶斯 + Parzen 窗密度估计：

$$P(x|c) = \prod_d \frac{1}{N_c}\sum_{i\in c} K\!\left(\frac{x_d - x_{i,d}}{\sigma}\right), \qquad \hat y = \arg\max_c P(x|c)P(c)$$

$K$ 为高斯核，$\sigma$ 由训练集内交叉验证选定。

**与 SVM 的根本区别**：SVM 只关心边界（支持向量），NBPW 用**全部训练样本**做密度估计，是生成式方法。

> **实测结论（见 07 号文档）：在这个数据规模下 NBPW 与线性 SVM 完全打平。**
> 冠军用 NBPW 可能是其多维/多类结构使然，不是因为 NBPW 本身更强。
> **因此 PL 上不需要实现 NBPW。**

---

## 4. 优化方向原理

### 4.1 ★ 欧氏对齐（Euclidean Alignment）

**原理**：不同 session 的协方差矩阵整体缩放与旋转不同，导致 CSP 滤波器失配。用本段数据自己的平均协方差白化，把所有域拉到同一参考系：

$$\tilde X = \bar R^{-1/2}X, \qquad \bar R = \frac{1}{n}\sum_{i=1}^{n}\frac{X_iX_i^T}{\mathrm{tr}(X_iX_i^T)}$$

**★ 对 FPGA 零成本——白化可以折叠进 CSP 投影**：

$$z = \tilde W^T\tilde X = \tilde W^T M X = (M^T\tilde W)^T X \quad\Longrightarrow\quad W_{\text{eff}} = M^T\tilde W$$

推理时仍是一次 $N\times n_{comp}$ 矩阵乘，**PL 一行代码都不用改**（CSP 投影模块本来就支持 W 重载）。

**均值类型的取舍**：算术均值与黎曼均值实测**完全相同**，故用算术均值（更简单、无迭代）。

**部署要求**：需要一段当前 session 的校准数据来算 $M$。校准量需求见 07 号文档。

**口径说明**：这会用到测试段的**无标签**数据算 $M$，属于标准的无监督域适应，在 EA 文献里是常规做法。写报告时必须明确标注。

### 4.2 EOG 回归剔除眼电

用线性回归把 EOG 成分从 EEG 里减掉（Gratton 法）：

$$X_{\text{eeg}} = A\,X_{\text{eog}} + b + \text{residual} \quad\Longrightarrow\quad \hat X_{\text{eeg}} = \text{residual}$$

**★ 实现方式决定成败**：
- **逐试次拟合系数** —— 一个 trial 只有几秒，拟合不稳定，会把**真实脑电一起减掉** → 有害
- **全局拟合系数** —— 整段数据估一组，稳定 → 有小幅正向效果

**离线做，零 PL 成本。** 实测增益很小（见 07 号文档），属收尾优化。

### 4.3 多时间窗集成

同一被试跑 2–3 个时间窗，特征拼接或决策投票。

**实测：与 EA 冲突**——EA 已经处理掉域偏移，多出来的窗主要带来噪声特征，稀释 MI 选择的有效性。
而且 PL 代价是 ×2~3。**故不采用。**

### 4.4 黎曼切空间（Tangent Space）

目前公认最强的经典方法：

$$C_i \to \text{黎曼均值} \to \text{白化} \to \log m(\cdot) \to \text{向量化} \to \text{线性分类器}$$

**与 EA 的关系**：EA 本质是黎曼方法的**零阶近似**（只白化，不做 `logm`）。
EA 已经吃掉了域偏移这块最大的收益，切空间的增量存疑。

**硬件代价**：`logm` 需要在线 Jacobi EVD，每 trial 一次。吞吐上可行（trial 率 0.4 Hz），
但**它在 trial 粒度工作，与"每 4 ms 一个决策"的卖点冲突**。

**结论：只做离线对比实验，不进 FPGA。**

### 4.5 CSSP / CSSD

**Common Spatio-Spectral Pattern**：给每个通道加 FIR 延迟抽头，再做 CSP——同时优化空间和**时间延迟**滤波器。
这是 2008 竞赛**第 2 名**用的方法。

- 实现：输入从 N 通道扩到 N×D（D 个延迟）
- 硬件：等效通道数 ×D，代价明确
- 增益：文献里稳定优于朴素 CSP，但**与 FBCSP 的增益可能重叠**，增量不明

**结论：优先级最低。**

### 4.6 深度网络

**文献现状与风险**：
1. **这个数据集太小**——9 被试 × 3 通道 × 400 试次，深度学习需要数据
2. **文献里的高数字很多有协议问题**——用评价集标签做模型选择、没做被试独立验证、与弱基线比
3. **开发成本是 FBCSP 的 3–5 倍**，且**验证困难**——FBCSP 可以和 MATLAB 逐值对拍，CNN 没有清晰的等价物

**结论：不部署。** 但可在 PC 上离线跑一次 EEGNet 做报告素材，以回应答辩时"考虑过深度学习吗"。

---

## 5. 一条贯穿全文的原则

优化项可以按"是否需要动硬件"分成两类：

| 类别 | 手段 | 说明 |
|---|---|---|
| **可折叠 / 离线**（PL 零改动） | 欧氏对齐、EOG 回归、被试特异频带、MIBIF 选择 | 白化/选择结果在 PC 或 PS 算好后，折进已有的参数矩阵 |
| **必须硬件化** | FBCSP 滤波器组、CSP 投影扩容、滑动窗扩容 | 改变数据通路的运算量 |

**优先做第一类。** 本项目的精度提升里，EA（最大单项）恰好属于第一类。

---

## 6. 相关文档

| 文档 | 内容 |
|---|---|
| **[07 验证结果与最终配置](07_verification_and_config.md)** | **所有实测数值、优化阶梯、最终配置** |
| [02 算法实现规格书](02_algorithm_spec.md) | 逐级公式 → 训练实现 → 定点 → PL 微架构 |
| [05 算法 FPGA 移植交接文档](05_fpga_porting_handoff.md) | PS/PL 任务分配与接口契约 |
