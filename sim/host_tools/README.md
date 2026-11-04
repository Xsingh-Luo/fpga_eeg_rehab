# tools —— 硬件调试与验证工具

| 文件 | 作用 |
|---|---|
| `cyton_sim_gui.py` | **图形界面版**：选串口、选数据集、实时看发送的波形 |
| `cyton_sim.py` | 命令行版 Cyton 串口模拟器（GUI 的核心逻辑复用它） |
| `cyton_parse.py` | **包解析器**：验证模拟器输出，同时是 Zynq 端 C / Verilog 解析逻辑的参考实现 |
| `openbci_record.py` | **真机采集**：Cyton 串口录音 + 左右手 MI 范式 + 事件表（支持 .bin 离线转 CSV） |
| `openbci_testbench.py` | **图形化测试台**：实时 8 通道波形、链路统计、录制、MI 提示屏、.bin 回放/转发 |
| `run_testbench.bat` | 双击启动上面的测试台（自动找 .venv\python.exe） |
| `mi_cue.py` | **独立左右手 MI 提示器**：中央十字 + 左/右箭头，不接 OpenBCI |
| `run_mi_cue.bat` | 双击启动上面的提示器 |
| `pget.py` | 多线程分块下载器（国际链路单连接被限速时提速约 20 倍） |
| `sample_stream.bin` | 744 包的样本字节流，供离线验证解析器 |
| `sample_stream.csv` | 上面那份的 CSV 导出（含计数与 µV 两套单位） |

---

## 图形界面（推荐）

```powershell
H:\FPGA2026QS\.venv\Scripts\python.exe H:\FPGA2026QS\tools\cyton_sim_gui.py
```

界面分四块：

| 区域 | 内容 |
|---|---|
| **输出** | 方式（串口/文件/TCP）、串口下拉+刷新、波特率、文件路径选择 |
| **数据集** | 格式（GDF/MAT）、路径（默认指向已下载的 BCI IV 2b）、被试/会话下拉（自动扫描）、通道 |
| **包参数** | 帧头 0x41/0xA0、AUX 模式、倍速、直流偏移、循环、等 'b' 命令；**实时显示带宽占用** |
| **波形图** | 8 个通道的滚动显示（5 秒窗口），ch1 在顶部；未接的通道显示为平线 |

底部状态栏实时给出：已发包数 · 实测速率 · 迟到次数与最大抖动 · 发送缓冲深度 · 实际通道名。

**支持的命令行预填**（方便复现场景）：

```powershell
# 直接发到 COM3
& $py cyton_sim_gui.py --port COM3

# 文件输出 + 自动开始（本机验证用，不需要硬件）
& $py cyton_sim_gui.py --sink file:demo.bin --source gdf --subject 4 --session 04E --autostart
```

| 参数 | 说明 |
|---|---|
| `--port COM3` | 预选串口 |
| `--sink file:路径` / `--sink tcp:端口` | 预选输出方式 |
| `--source gdf\|mat` | 预选数据格式 |
| `--subject N` / `--session xx` | 预选被试与会话 |
| `--autostart` | 启动后立即开始发送 |
| `--list` | 列出串口后退出 |

---

## 命令行版

```powershell
cd H:\FPGA2026QS\tools
$py = "H:\FPGA2026QS\.venv\Scripts\python.exe"

# 列出串口 / 干跑看包格式（先做这一步！）
& $py cyton_sim.py --list
& $py cyton_sim.py --dry-run --n 5

# 发到串口，等 Zynq 发 'b' 再开始（模拟真实 Cyton 行为）
& $py cyton_sim.py --port COM3

# 不等命令直接开跑，打上时间戳（测延迟用）
& $py cyton_sim.py --port COM3 --no-wait --subject 4 --session 04E --aux time

# 不用硬件，写到文件里验证
& $py cyton_sim.py --sink file:out.bin --subject 1 --session 01T
& $py cyton_parse.py --input file:out.bin --csv out.csv
```

**常用参数**

| 参数 | 说明 |
|---|---|
| `--source gdf\|mat\|bin` | `gdf`=原始连续数据（推荐，最贴近流式）；`mat`=MATLAB 导出（试次拼接）；`bin`=实采 .bin 原样回放 |
| `--bin 路径` | `--source bin` 的原始字节流；默认把 `0xA0` 帧头改写为 `0x41`（`--rewrite-header none` 可保留原样） |
| `--subject N` / `--session xx` | 选被试与会话，如 `--subject 4 --session 04E` |
| `--channels C3,Cz,C4` | 映射到 Cyton 通道 1..N，其余填 0 |
| `--dc-offset µV` | 注入直流偏移，测试 Zynq 端的去 DC 逻辑 |
| `--speed N` | 播放倍速。⚠️ 受波特率限制，115200 下最高约 1.4× |
| `--sink file:路径` / `tcp:端口` | 替代串口输出，用于离线验证或以太网通路 |

---

## OpenBCI 真机实采（Cyton）

`cyton_sim*.py` 是“没有硬件时”的替代品。真机采数用 `openbci_record.py`：
它直接从 Dongle 串口抓 33 字节包，存 `.bin` + `.csv` + `events.csv` + `meta.json`。

```powershell
$py = "H:\FPGA2026QS\.venv\Scripts\python.exe"
cd H:\FPGA2026QS

& $py tools\openbci_record.py --list
& $py tools\openbci_record.py --port COM3 --duration 30 --name subj01_check
& $py tools\openbci_record.py --port COM3 --paradigm mi2 `
    --trials-per-class 10 --blocks 3 --name subj01_mi --hw-marker
```

- `--paradigm mi2`：3 s 注视 + cue + 4 s 想象 + 4 s 休息，与 BCI IV 2b 对齐；60 trial 约 12 分钟。
- `--hw-marker`：把 cue 写进 AUX1（固件需支持 `/4` marker 模式）；不写也有事件表。
- 采完先 `cyton_parse.py` 校验，再 `cyton_sim.py --source bin` 回放给 PL。

电极蒙太奇、阻抗、被试指导语、质控清单见 **[../../report/design/14_openbci_acquisition.md](../../report/design/14_openbci_acquisition.md)**。

---

### 图形化测试台（推荐先跑这个）

命令行之外，另有一个 Tkinter 测试台，适合接真机时看波形和查链路：

```powershell
$py = "H:\FPGA2026QS\.venv\Scripts\python.exe"
cd H:\FPGA2026QS
& $py tools\openbci_testbench.py

# 没有硬件时先看界面：自动载入 sim/host_tools/sample_stream.bin 循环回放
& $py tools\openbci_testbench.py --demo
```

- 串口模式：连 Cyton，实时看 8 通道波形、包率、丢包、重同步，并自动录制；
- 文件模式：按 250 Hz 回放 `.bin`，可循环、调速，并可转发到另一个串口；
- 运动想象：弹出全屏提示屏，按 2b 时序跑 cue，事件写 `*_events.csv`；
- 自检：包数、丢包、CH1–CH3 是否全 0 / 贴轨。

也可以直接双击 `run_testbench.bat` 启动。

输出优先落 `data/raw/openbci/gui/`；若该目录只读（例如沙箱里），自动落到 `sessions/`。

完整操作与被试指导见 **[../../report/design/14_openbci_acquisition.md](../../report/design/14_openbci_acquisition.md)**。

### 辅助想象提示器（独立，不需要 OpenBCI）

如果只想给被试放左右手箭头，用 [mi_cue.py](mi_cue.py)；它不连串口、不写 EEG，只负责视觉提示和事件表。

```powershell
$py = "H:\FPGA2026QS\.venv\Scripts\python.exe"
cd H:\FPGA2026QS
& $py tools\mi_cue.py

# 窗口模式 + 提示音（方便放第二块屏 / 排练）
& $py tools\mi_cue.py --windowed --beep
```

- 空格：开始 / 暂停 / 继续；Esc：中止；F11：全屏；
- 3 s 注视 + 1.25 s 箭头 + 4 s 想象 + 4 s 休息；默认 3 组 × 20 trial ≈ 12 分钟；
- 事件表默认写 `sessions/mi_cue/*_events.csv`（wall time + 相对时间）；
- 和 EEG 严格对齐请用测试台的 MI 页（事件带采样序号），这个独立程序只做提示。

也可以直接双击 `run_mi_cue.bat` 启动。

## 为什么需要模拟器

Cyton 硬件不在手上时，Zynq 端的 UART 接收 + 包解析模块没法调试。
这个模拟器用真实开源数据集产生**格式完全一致**的字节流，让 Zynq 端可以先跑起来。

数据用的是 BCI Competition IV 2b（真实 EEG，不是合成信号），
而且包格式是照着 Cyton 固件 `sendChannelDataSerial()` 逐字节构造的，见下。

## 包格式（33 字节 @ 250 Hz）

| 偏移 | 长度 | 内容 |
|---|---|---|
| 0 | 1 | 帧头。**默认 `0x41`**（固件 `OPENBCI_BOP='A'`，PIC32→RFduino 那条 UART 上的首字节）；`--header a0` 可切成 `0xA0`（dongle 输出给 PC 的格式） |
| 1 | 1 | 采样序号 0–255 回绕 |
| 2 | 24 | 8 通道 × 24 位有符号，**大端，MSB 在前** |
| 26 | 6 | AUX 字节，含义由帧尾决定 |
| 32 | 1 | 帧尾 = `0xC0 \| packetType` |

换算：`count = µV / 0.022352`（= 4.5 V / gain24 / (2²³−1)）

**AUX 模式**（`--aux`）

| 值 | 帧尾 | AUX 内容 |
|---|---|---|
| `zero`（默认） | `0xC0` | 全 0 |
| `aux` | `0xC1` | 全 0 |
| `time` | `0xC6` | 后 4 字节为 32 位毫秒时间戳，**可用来测端到端延迟** |

> ⚠️ Zynq 端解析器应同时接受 `0x41` 和 `0xA0` 两种帧头，并把帧尾判据写成
> `(footer & 0xF0) == 0xC0`，这样四种 AUX 类型都能兼容。

---

## 接线（CH340 → Zynq PS）

```
CH340 TX  ──►  Zynq PS UART RX
CH340 GND ──   Zynq GND          ← 必须共地
```

- ⚠️ **CH340 模块若有 5V/3.3V 跳线，必须切到 3.3 V**，否则可能打坏 Zynq
- ⚠️ 先确认 Zynq 的 PS UART 引脚在哪个接口上（PYNQ-Z2 的 PS UART0 通常接到了 micro-USB 调试口），
  以及该 MIO bank 的电压（1.8 V / 3.3 V）
- 波特率 **115200 8N1**

## 已验证

本机用 6 秒捕获做的端到端验证：

```
完整包     : 1488
收到字节   : 49104  →  有效率 100.00%
重新同步   : 0 次
序号连续性 : ✓ 无丢包
时间戳     : 相邻间隔中位数 4 ms，抖动 0.30 ms（理想 4 ms）
通道幅度   : C3/Cz/C4 标准差约 5.4 µV —— 与 BCI IV 2b 原始数据一致
通道 4-8   : 全 0（未接电极）
```

---

## 带宽约束（很重要）

`33 B × 250 Hz = 8250 B/s`，115200 8N1 的容量是 11520 B/s
→ **占用 71.6%，只剩 1.14 ms/样本 的余量**。

所以：
- **不要试图用 115200 跑倍速回放**（最高约 1.4×）
- 想加速就提波特率，或降低通道数/采样率
- 模拟器会实时报告"迟到次数"和发送缓冲深度，超了就说明时序撑不住

---

## 下一步

`cyton_parse.py` 里的 `CytonParser` 类就是 Zynq 端要实现的**状态机**：

1. 缓冲区里找帧头（`0x41` 或 `0xA0`）
2. 凑够 33 字节
3. 校验第 33 字节落在合法帧尾集合
4. 合法 → 输出；不合法 → 帧头后移一字节重新同步

把它翻成 Verilog（或 HLS）就是 PL 侧的 `frame_parser` 模块，
可以直接用 `sample_stream.bin` 做仿真激励。
