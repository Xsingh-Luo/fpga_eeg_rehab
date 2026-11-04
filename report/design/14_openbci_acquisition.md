# 14 · OpenBCI 实采与左右手运动想象范式

> 版本 v1.0 ｜ 适用：OpenBCI Cyton（8 通道）+ USB Dongle + OpenBCI GUI / Python
> 配套工具：[sim/host_tools/openbci_record.py](../../sim/host_tools/openbci_record.py)（采集 + 范式）、
> [sim/host_tools/cyton_sim.py](../../sim/host_tools/cyton_sim.py)（`--source bin` 回放）、
> [sim/host_tools/cyton_parse.py](../../sim/host_tools/cyton_parse.py)（校验）
>
> 本文只管“在 PC 上采一份能进本项目的数据”。真机接到 Zynq 时的**电气隔离**要求
> 见 [04 号文档](04_uart_handoff.md) §2，不能省。

---

## 0. 一句话流程

戴帽 → 查阻抗 → 关掉 OpenBCI GUI → 跑 `openbci_record.py` → 得到
`*_raw.bin / *_raw.csv / *_events.csv / *_meta.json` → 用 `cyton_parse.py` 校验 →
用 `cyton_sim.py --source bin` 回放给 PL。

---

## 1. 硬件与安全

| 项 | 做法 |
|---|---|
| 供电 | Cyton 用**锂电池**；采集时笔记本尽量用**电池**，不要边充电边采 |
| 板子开关 | 拨到 **PC** 档（不是 Analog）；上电后确认指示灯正常 |
| Dongle | 插 USB；Windows 设备管理器应出现 `USB Serial Port (COMx)`（FTDI FT231X，VID:PID=0403:6015） |
| 驱动 | 没 COM 口时装 FTDI VCP 驱动；装完重新插拔 Dongle |
| 安全 | Cyton 电池供电时被试是浮地的。**一旦用导线把 Cyton 接到 USB 供电的 Zynq，必须按 04 §2 做数字隔离**（TX/RX/GND 都要隔离），否则被试身上会出现到市电地的通路 |

> 采集前把手机、充电器、大功率显示器挪远；电极线不要盘在电源适配器上。

---

## 2. 电极与蒙太奇

### 2.1 8 通道推荐（CH1–CH3 必须按这个顺序）

| Cyton | 10-20 位置 | 作用 |
|---|---|---|
| **CH1** | **C3** | 右手 MI 的对侧（左半球） |
| **CH2** | **Cz** | 中线，辅助 |
| **CH3** | **C4** | 左手 MI 的对侧（右半球） |
| CH4 | FC3 | 可选，扩展 8 通道模型 |
| CH5 | FC4 | 可选 |
| CH6 | CP3 | 可选 |
| CH7 | CP4 | 可选 |
| CH8 | Pz | 可选 |

> **为什么 CH1–CH3 必须是 C3/Cz/C4**：当前 PL 的 `N_CH=3`，只取包里的前 3 个通道
> （`eeg_csp_svm_top.v` 的 `x_dc` 循环）。顺序错了，模型拿到的就是错通道。
> 8 通道数据可以原样回放，但 3 通道模型仍然只用前 3 路；要真正用 8 路，得把
> `N_CH` 改成 8 并重新训练/导出参数。

### 2.2 参考与地

- **SRB** 接参考电极：左乳突（M1）或双乳突并联；也可用耳垂，但**整个项目要统一**。
- **BIAS** 接地：右乳突（M2）或对侧耳垂。
- 参考/地的阻抗也要查——只查 8 个记录电极是不够的。
- 不用的通道：在 GUI 里关掉，或把输入短接到 SRB；**不要悬空**，悬空容易飘到轨。

### 2.3 阻抗

- 目标：**< 10 kΩ**；能压到 **< 5 kΩ** 更好。
- 干电极/少发部位先酒精棉擦皮肤，再打导电膏；头发拨开，确保膏接触到头皮。
- 每组之间摸一下阻抗；高了就补膏。
- 阻抗一边高一边低，会直接让 CSP 的空间滤波器学歪。

---

## 3. 软件与第一次出数

### 3.1 先用 GUI 确认硬件

1. 打开 OpenBCI GUI → 选 **CYTON** → 选 Dongle 的 COM 口 → 采样率 **250 Hz** →
   `START SYSTEM`。
2. 看 8 条波形：应是 µV 级噪声，不应贴顶/贴底；轻碰电极能看到变化。
3. 打开 **Impedance Check**，逐个通道看阻抗；不合格先处理。
4. 确认没问题后**关闭 GUI**。

> ⚠️ 串口是单消费者：GUI 开着，Python 会报“打不开 COMx / port already open”。
> 采集前必须关掉 GUI（以及任何其它串口助手）。

### 3.2 用本项目 recorder 采

```powershell
$py = "H:\FPGA2026QS\.venv\Scripts\python.exe"
cd H:\FPGA2026QS

# 看串口
& $py tools\openbci_record.py --list

# 先空采 30 s，确认能出数、丢包为 0
& $py tools\openbci_record.py --port COM3 --duration 30 --name subj01_check
```

正常输出末尾应有：

```
✓ 序号连续，无丢包
```

如果 `丢包 > 1%`：换 USB 口/不用 Hub、换短线、关掉占 CPU 的程序，再采一次。
OpenBCI 官方口径是**丢包 > 1% 这份记录不可用**。

### 3.3 采左右手 MI

```powershell
& $py tools\openbci_record.py --port COM3 --paradigm mi2 `
    --trials-per-class 10 --blocks 3 --name subj01_sess01_mi --hw-marker
```

- `--trials-per-class 10 --blocks 3` = 每组 20 trial（左右各 10），共 **60 trial**。
- 全屏黑底；按 **空格** 开始；**Esc** 中止。
- `--hw-marker` 可选：让 Cyton 把 cue 写进下一包的 AUX1（4 ms 分辨率）。
  固件不支持 `/4` 时脚本会提示，并退回“事件表按采样点对齐”，不影响采数。
- 想排练：加 `--auto-start --windowed`，并把 `--fixation/--cue/--imagery/--rest`
  调小，跑一遍看画面。

### 3.4 图形化测试台（接真机时推荐）

如果不想记命令行，用 `sim/host_tools/openbci_testbench.py`：

```powershell
& $py tools\openbci_testbench.py

# 没有硬件时先看界面
& $py tools\openbci_testbench.py --demo
```

界面里能做的事：

- **数据源**：串口 Cyton 或 `.bin` 文件回放；
- **时域波形**：8 通道滚动 5 s，自动量程/去直流；
- **频谱**：CH1–CH3 的 Welch PSD，用于看 mu 节律和 50 Hz；
- **链路状态**：包率、丢包、重同步、时长、marker；
- **录制**：串口模式自动存 `*_raw.bin/csv/meta.json`；
- **运动想象**：点“打开提示屏并开始”，全屏按 2b 时序跑，事件写 `*_events.csv`；
- **回放/转发**：`.bin` 按 250 Hz 发到另一个串口（默认 0xA0→0x41），可暂停/继续；
- **自检**：一键检查包数、丢包、CH1–CH3 是否全 0 或贴轨。

> 提示屏会置顶全屏，运行期间操作台被盖住是正常的；`Esc` 或界面上的“中止”可停。
> 输出优先落 `data/raw/openbci/gui/`；该目录只读时自动落 `sessions/`。

### 3.5 独立提示器（不接 OpenBCI）

如果只想让被试练左右手、或者先做指导语演练，不采集 EEG，直接用
[sim/host_tools/mi_cue.py](../../sim/host_tools/mi_cue.py)：

```powershell
& $py tools\mi_cue.py

# 窗口模式 + cue 提示音；放在第二块屏上
& $py tools\mi_cue.py --windowed --beep --geometry 1100x720+1920+0
```

- 空格：开始 / 暂停 / 继续；Esc：中止；F11：全屏；
- 默认 3 组 × 20 trial，3 s 注视 + 1.25 s 箭头 + 4 s 想象 + 4 s 休息；
- 事件表默认写 `sessions/mi_cue/*_events.csv`，含 wall time 和相对时间；
- 这个程序不碰串口、不写 EEG。要和 EEG 严格对齐，用 3.4 的测试台 MI 页
  （事件按采样序号记录，可选 AUX marker）。

---

## 4. 采什么：本项目的最小测试数据

建议一次采三份（同一被试、同一天、同一蒙太奇）：

| 文件 | 内容 | 时长 | 用途 |
|---|---|---|---|
| `subj01_sess01_rest_2min` | 睁眼静息，无 cue | 2 min | 测直流偏移、噪声、串口吞吐 |
| `subj01_sess01_mi_60` | 60 个左右手 trial + events | ~11 min | 端到端回放、准确率 |
| `subj01_sess01_artifacts_1min` | 眨眼/咬牙/吞咽/轻微头动 | 1 min | 测伪迹鲁棒性（可选） |

存放：

```
data\raw\openbci\20261027\
    subj01_sess01_rest_2min_raw.bin
    subj01_sess01_rest_2min_raw.csv
    subj01_sess01_rest_2min_meta.json
    subj01_sess01_mi_60_raw.bin
    subj01_sess01_mi_60_raw.csv
    subj01_sess01_mi_60_events.csv
    subj01_sess01_mi_60_meta.json
```

`data/` 已在 `.gitignore` 里，**不要提交**；自己备份到移动硬盘/网盘。

---

## 5. 保存格式与回放

### 5.1 `bin` 是什么

`*_raw.bin` 是**只由合法 33 字节包首尾相接**组成的字节流（不含任何文件头），
和 `sim/host_tools/sample_stream.bin` 同构。所以：

- `cyton_parse.py` 可以直接读；
- `cyton_sim.py --source bin` 可以原样回放；
- 丢失的包不补零，`sample_index` 会按序号跳变（`meta.json` 里有 `lost_pct`）。

### 5.2 先校验

```powershell
& $py tools\cyton_parse.py --input file:data\raw\openbci\20261027\subj01_sess01_mi_60_raw.bin `
    --csv data\raw\openbci\20261027\check.csv
```

看四项：完整包数、有效率 100%、序号连续性 ✓、各通道 µV 标准差（EEG 一般 5–50 µV）。
若 CH1–CH3 全是 0，说明电极没接上或通道被关掉。

### 5.3 回放给 PL

```powershell
# 默认会把 0xA0 帧头改写成 0x41（当前 pkt_parser.v 只认 0x41）
& $py tools\cyton_sim.py --source bin `
    --bin data\raw\openbci\20261027\subj01_sess01_mi_60_raw.bin --port COM4

# 要逐字节原样（比如给 PS 的软件解析器对拍）
& $py tools\cyton_sim.py --source bin --bin ... --port COM4 --rewrite-header none
```

> ⚠️ **0xA0 / 0x41 的坑**：Dongle 输出给 PC 的首字节是 `0xA0`；Cyton 的 D11/D12
> 和本项目模拟器默认是 `0x41`。当前 RTL `pkt_parser.v` 只搜 `0x41`，所以回放
> 真实 .bin 时要么让 `cyton_sim` 改写（默认），要么把 RTL 改成同时接受两者。
> 这件事在 [01 号文档](01_fixed_point_interface.md) §3.1 和 README 里都写了“应同时接受”，
> 但 RTL 还没改——上板前记得确认。

### 5.4 3 通道模型 vs 8 通道数据

- 当前 `params/csp_svm_params.h`：`N_CH=3`，模型是在 BCI IV 2b 上训练的。
- 实采 8 通道回放时，PL 只用 CH1–CH3；CH4–CH8 不参与运算。
- 2b 的受试者、参考、硬件和你的现场不同，**不要指望直接套参数就有 2b 的准确率**。
  第一目标是用真实数据把“串口 → 解析 → 滤波 → CSP → SVM → 显示”整条路跑通；
  第二目标才是用自采数据重新训练 CSP/SVM 并导出参数。

---

## 6. 左右手 MI 范式（与 2b 对齐）

### 6.1 单 trial 时序

| 阶段 | 时间（相对 trial 起点） | 屏幕 | 事件 |
|---|---|---|---|
| 注视 | 0 – 3.0 s | 中央十字 | `trial_start`（marker 3） |
| cue | 3.0 – 4.25 s | 中央箭头（左/右） | `cue`（marker 1/2） |
| 想象 | 3.0 – 7.0 s（共 4 s） | 十字 | — |
| 休息 | 7.0 – 11.0 s（4 ± 1 s） | 黑屏 | `imagery_end` |

- 2b 的 768（trial 起点）到 769/770（cue）正好 **3.0 s**；项目的分析窗
  **[3.5, 6.0] s** 就是 **cue 后 0.5–3.0 s**。
- 所以**不要**把注视期改成 2 s 或 1 s，否则窗口整体错位；除非你重新训练并改窗。
- 60 trial = 3 组 × 20（左右各 10），组间休息 20 s；总时长约 12 min。

### 6.2 为什么 cue 必须放在中央

- 箭头放在屏幕左边/右边会诱导**眼动**，眼动伪迹（EOG）会污染 C3/C4，CSP 会学到眼动而不是 MI。
- 本脚本把箭头画在中央，只改变箭头方向；被试眼睛始终盯十字。

### 6.3 试次顺序

- 左右 1:1，随机打乱；
- 不允许连续 3 个同类（脚本已处理）；
- 每组重新随机；不要固定“左-右-左-右”，否则被试会预判。

---

## 7. 被试指导语（照读）

### 7.1 开始前

让被试坐舒服：背靠椅背、双手放扶手、双脚踩地、屏幕距离约 60–70 cm。
说：

> “接下来大约 10 分钟，我们做左右手的运动想象。
> 屏幕中央会一直有一个十字，请一直看着它。
> 十字出现 3 秒后，会出现一个箭头。箭头向左，就想象**左手**；箭头向右，就想象**右手**。
> 想象的时候，注意是**手在张开、握紧的感觉**，像真的在动，但手不要真的动。
> 不要去想‘我在看我的手’，也不要在心里念‘左、右’，把注意力放在手的感觉上。
> 如果走神了，没关系，轻轻把注意力拉回来。
> 头不要转，眼睛盯着十字，尽量少眨眼，不要咬牙、吞咽、说话。
> 每组结束会休息 20 秒。有任何不舒服，随时告诉我。”

### 7.2 必须讲清楚的五件事

| 要 | 不要 |
|---|---|
| **动觉**：想象肌肉发力/放松、手掌张开握紧的本体感觉 | **视觉**：想象“看见”自己的手在动 |
| **第一人称**：像自己真的在做 | **第三人称**：像看别人做 |
| **持续 4 s**：cue 出现后一直保持，不要想一下停一下 | **只闪一下**：cue 一过就停 |
| **放松**：手、肩、下巴都不用力 | **真动/绷紧**：手指、手腕、手臂实际收缩 |
| **稳定**：头、眼、下巴不动，自然呼吸 | **眼动/吞咽/咬牙/说话** |

### 7.3 练习与纠偏

- 正式记录前做 **6–10 个练习 trial**（可以不存，或存了标 `practice`）。
- 练完问三个问题：
  1. “箭头向左时，你想象的是哪只手？”——确认没有左右反。
  2. “你是在‘感觉手在动’，还是在‘看手在动’？”——后者要纠正。
  3. “手有没有真的用力？”——有的话，让他把手完全放松再试。
- 如果被试说“两只手都在想”，让他只关注箭头那只手，另一只手想象成“休息”。
- 如果被试说“我在心里念左/右”，让他改成感受手掌/手指的变化。
- 如果被试越做越紧张，停下来放松 1 分钟；紧张产生的 EMG 比 MI 信号大得多。

### 7.4 常见失败模式

| 现象 | 可能原因 | 处理 |
|---|---|---|
| C3/C4 上高频毛刺 | 咬牙、皱眉、肩颈紧张 | 提醒放松；必要时重采 |
| 慢漂/大幅波动 | 眼动、出汗、电极松动 | 盯十字；补膏；固定线 |
| 左右完全分不开 | 视觉想象、双手同时想、疲劳 | 改指导语；缩短组；换策略（握球/开瓶盖） |
| 被试说“很难受/困” | 疲劳、姿势 | 休息；不要硬撑 |
| 准确率长期 ~50% | BCI 不敏感（约 15–30% 的人） | 换策略/换被试；不要调参硬凑 |

---

## 8. 质控清单

采完一份，至少检查：

- [ ] `meta.json` 里 `lost_pct < 1%`（最好 0）
- [ ] `cyton_parse.py` 显示 `有效率 100%`、`序号连续性 ✓`
- [ ] CH1–CH3 的 µV 标准差在 5–50 µV，不是 0，也没有贴轨
- [ ] 阻抗 < 10 kΩ（记录在 `meta.json` 旁边的手写/备注里）
- [ ] `events.csv` 里每个 trial 都有 `trial_start` 和 `cue`，左右数量 1:1
- [ ] 回放能出数：`cyton_sim --source bin --bin ... --sink file:test.bin` 后
      `cyton_parse` 仍 100%
- [ ] 备份到 `data/raw/openbci/` 之外的位置

---

## 9. 命令速查

```powershell
$py = "H:\FPGA2026QS\.venv\Scripts\python.exe"
cd H:\FPGA2026QS

# 图形化测试台（推荐）
& $py tools\openbci_testbench.py
& $py tools\openbci_testbench.py --demo

# 只放左右手提示（不采集）
& $py tools\mi_cue.py

# 串口
& $py tools\openbci_record.py --list

# 30 s 自检
& $py tools\openbci_record.py --port COM3 --duration 30 --name subj01_check

# 静息 2 min
& $py tools\openbci_record.py --port COM3 --duration 120 --name subj01_rest_2min

# 60 trial MI（3 组 × 20）
& $py tools\openbci_record.py --port COM3 --paradigm mi2 `
    --trials-per-class 10 --blocks 3 --name subj01_mi_60 --hw-marker

# 排练（短时序 + 窗口 + 自动开始）
& $py tools\openbci_record.py --input file:tools\sample_stream.bin `
    --paradigm mi2 --trials-per-class 1 --blocks 1 `
    --fixation 0.5 --cue 0.5 --imagery 1.0 --rest 0.5 --block-rest 1 `
    --windowed --auto-start --out .tmp_rehearsal

# 校验
& $py tools\cyton_parse.py --input file:..._raw.bin --csv ..._check.csv

# 回放给 PL
& $py tools\cyton_sim.py --source bin --bin ..._raw.bin --port COM4
```

---

## 10. 相关文档

- [04 串口接入交接文档](04_uart_handoff.md)：33 字节包、PL UART、隔离要求
- [01 定点格式与接口约定](01_fixed_point_interface.md)：帧头/帧尾、丢包补零
- [02 算法实现规格书](02_algorithm_spec.md)：[3.5, 6.0] s 窗、CSP/SVM
- 00 总体实施规划：自采数据在整体计划中的位置
