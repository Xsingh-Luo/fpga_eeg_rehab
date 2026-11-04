# 04 · Cyton 串口接入交接文档

> 上下游：00 总体实施规划 · [02 算法实现规格书](02_algorithm_spec.md)
> 本文档定义 **Cyton 数据流 → Zynq 处理链** 的接口契约，是"采集侧"与"算法侧"的交接依据。
> 版本 v1.0 ｜ 所有 Cyton 侧参数均来自固件源码，非二手资料

---

## 0. 一句话结论

**串口接 PL，由 PL 做切分。** PS 只做配置和显示。

但**分两步走**：先用 PS 打通链路（一天的事），再用 PL 落地。PS 版本不是浪费——它是 PL 版本的**逐 bit 对拍金标准**，也是决赛的降级演示路径。

---

## 1. 架构决策

### 1.1 结论与依据

| 维度 | 接 PS（MIO UART） | **接 PL（fabric UART）** |
|---|---|---|
| 引入延迟 | ms 级，且**有调度抖动** | **接近于零，零抖动** |
| 丢字节风险 | 有（见 §1.2） | 硬件 FIFO，理论上不会 |
| 引脚可及性 | PS MIO 基本在板内，需 EMIO 才能引出 | **PMOD / Arduino 排针直接可用** |
| PS CPU 占用 | 250 Hz 中断 + 解析 | 0 |
| 与"确定性延迟"卖点 | ❌ 直接冲突 | ✅ 一致 |
| 开发成本 | 低（PYNQ 几行 Python） | 中（一个 UART RX + 状态机，约 200 行 RTL） |

**决定性理由是延迟和确定性。** 这个项目的核心卖点就是"确定性"，把串口放在 PS 上等于在最前端重新引入了你要消除的东西。

### 1.2 为什么 PS 方案有实质风险（不是理论担忧）

| 项 | 计算 | 值 |
|---|---|---|
| 115200 8N1 容量 | 115200 / 10 | 11520 B/s |
| 实际负载 | 33 B × 250 Hz | 8250 B/s（**占用 71.6%**） |
| 单字节时间 | 10 / 115200 | **86.8 µs** |
| 单包时间 | 33 × 86.8 µs | **2.864 ms** |
| 采样周期 | 1/250 | 4 ms |
| **每周期空闲** | 4 − 2.864 | **1.136 ms（28.4%）** |
| Zynq PS UART RX FIFO | 64 字节 | **5.55 ms 缓冲** |

**关键**：Cyton 数据流**没有流控**（no flow control），主机必须及时取走。PS 侧一旦被阻塞超过 **5.55 ms**（SD 卡写入、Python GC、调度抢占），就会丢字节 → 整包失步 → 必须重新搜同步字。

PL 侧不存在这个问题。

### 1.3 分阶段落地（与 00 号文档 P2/P3 对齐）

| 阶段 | 做什么 | 产出与作用 |
|---|---|---|
| **P2** | PS 用 PYNQ/Python 读串口并解析 | ① 验证 Cyton 真能出数 ② **PL 版本的对拍金标准** ③ 决赛降级路径 |
| **P3** | PL 实现 UART RX + 包解析 | 最终架构，零抖动 |
| **P3 后** | PS 版本保留为回归测试工具 | 每次改 PL 代码都跑一遍比对 |

---

## 2. ⚠️ 安全要求（先看这条）

**Cyton 是锂电池供电、与市电解耦的——这是被试安全设计。**

一旦你用导线把 FPGA 开发板（USB 供电、接地）与 Cyton 连起来，就**在被试身上建立了一条到市电地的通路**，隔离被破坏。

OpenBCI 官方对同样的问题的原话是：

> "The most important thing is making sure that you can't accidentally plug yourself into the mains electrical supply."

**必须做下面任一项**：

| 方案 | 成本 | 说明 |
|---|---|---|
| **数字隔离器**（推荐） | ¥10–20 | ADuM1201 / Si8620 / ISO7221，串在 TX/RX 两根信号线上。**GND 也要隔离**，用隔离器的双电源 |
| USB 隔离器 | ¥50–150 | ADuM4160 方案，隔离整块 FPGA 板的 USB |
| FPGA 板用电池供电 | ¥50+ | 移动电源即可，最省事 |

> 注意：**只连 TX/RX/GND 三根线、不连电源**并不能解决这个问题——GND 一接，参考地就连上了。

---

## 3. 物理层接口

### 3.1 信号定义

| 信号 | 方向（相对 FPGA） | Cyton 侧 | 说明 |
|---|---|---|---|
| `UART_RX` | **输入** | D11（PIC32 Serial1 TX） | 数据流，33 字节包 |
| `UART_TX` | **输出** | D12（PIC32 Serial1 RX） | 命令（`b` 开始 / `s` 停止）——**需实测是否被固件接受** |
| `GND` | — | GND | **必须共地**（经隔离器） |
| `3V3` | — | 3V3 | 仅作电平参考，**不要用它给 FPGA 板供电** |

### 3.2 电气参数（固件注释原文）

> "You will need to reflash your board! But now you can connect to pins `11` and `12` via a FTDI serial port driver... **Remember to use 3V3, 115200 baud, and have a common ground!**"

| 参数 | 值 |
|---|---|
| 电平 | **3.3 V TTL**（不可接 5 V，会打坏 PIC32） |
| 波特率 | **115200**，8-N-1，无流控 |
| Zynq bank 电压 | 必须设 **3.3 V**（LVCMOS33） |

### 3.3 引脚分配（以 PYNQ-Z2 / 同类板为例）

```
Cyton D11 (TX) ──[隔离器]──► PMODA pin 1  → FPGA UART_RX
Cyton D12 (RX) ◄─[隔离器]──  PMODA pin 2  ← FPGA UART_TX
Cyton GND      ───────────   PMODA pin 5  (GND)
```

XDC 约束（信号名与引脚按实际板卡改）：

```tcl
# ---- Cyton UART ----
set_property -dict { PACKAGE_PIN Y18  IOSTANDARD LVCMOS33 } [get_ports uart_rx]
set_property -dict { PACKAGE_PIN Y19  IOSTANDARD LVCMOS33 } [get_ports uart_tx]
# 输入同步 + 去抖（跨时钟域，虽然同为异步信号）
set_property ASYNC_REG TRUE [get_cells -hier -filter {NAME =~ *sync_rx_reg*}]
```

> `uart_rx` 是异步输入，**必须**先过两级同步器再进状态机。

---

## 4. 链路层：Cyton 数据包（固件源码确认）

### 4.1 固件证据

```cpp
#define OPENBCI_BOP  'A'      // = 0x41，包起始字节
#define PCKT_END     0xC0     // 包尾基值

void OpenBCI_32bit_Library::sendChannelDataSerial(PACKET_TYPE packetType) {
  writeSerial(OPENBCI_BOP);                       // 1 byte - 0x41
  writeSerial(sampleCounter);                     // 1 byte
  ADS_writeChannelData();                         // 24 bytes
  ...                                             // 6 bytes aux
  writeSerial((uint8_t)(PCKT_END | packetType));  // 1 byte
}
```

**★ 关键结论**：PIC32↔RFduino 这条 UART 上与 PC 端文档**逐字节一致**，**唯一差别是首字节 `0x41` 而非 `0xA0`**。

**★ 另一个关键结论**：`writeSerial()` 同时写 `Serial0`（到 RFduino）和 `Serial1`（D11/D12 debug）：

```cpp
void OpenBCI_32bit_Library::writeSerial(uint8_t c) {
    if (iSerial0.tx) { Serial0.write(c); }
    if (iSerial1.tx) { Serial1.write(c); }   // ← 与上面字节完全相同
}
```

所以**走 D11/D12 和焊线监听 PIC32↔RFduino UART，拿到的数据是一样的**——而 D11/D12 好焊得多。

### 4.2 33 字节包格式

| 索引 | 长度 | 字段 | 处理 |
|---|---|---|---|
| 0 | 1 | **`0x41`** 包起始 | 同步字节；搜到它才开始收 |
| 1 | 1 | `sampleCounter` 0–255 回绕 | **丢包检测依据** |
| 2–25 | 24 | 8 通道 × 3 字节，**24 位有符号，大端（MSB 在前）** | 主数据 |
| 26–31 | 6 | AUX，含义由包尾决定 | 默认是加速度计；可忽略 |
| 32 | 1 | **`0xC0 \| packetType`** | 校验 `(b & 0xF0) == 0xC0` |

**packetType 取值**（固件枚举，0 起）：

| 值 | 名称 | 包尾 |
|---|---|---|
| 0 | `PACKET_TYPE_ACCEL`（默认） | `0xC0` |
| 1 | `PACKET_TYPE_RAW_AUX` | `0xC1` |
| 2 | `PACKET_TYPE_USER_DEFINED` | `0xC2` |
| 3–6 | 各种带时间戳模式 | `0xC3`–`0xC6` |

**校验规则**：`(footer & 0xF0) == 0xC0` 即合法；`footer & 0x0F` 给出 AUX 类型。

**通道提取**（通道 c = 0..7）：

```verilog
b0 = buf[2+3*c];  b1 = buf[3+3*c];  b2 = buf[4+3*c];
val = {b0, b1, b2};                  // 24 位有符号，大端
// 需要 32 位时：{ {8{val[23]}}, val }
```

**物理量换算**（供显示用，PL 内部不需要）：`µV = count × 0.02235`（gain = 24）。

### 4.3 启动时序

```
1. Cyton 上电，等约 1 s
2. 发 'v'  → 复位内部状态，回送若干行 ASCII，以 '$$$' 结尾
3. 发 'b'  → 开始持续推流（250 包/秒）
4. 发 's'  → 停止
```

⚠️ 32 位板**打开串口不会自动复位**，第 2 步的 `v` 不能省。

---

## 5. 带宽与时间预算

| 项 | 值 | 备注 |
|---|---|---|
| 位时间 | 8.68 µs | |
| 字节时间 | **86.8 µs** | 10 位 |
| 包时间 | **2.864 ms** | 33 字节连发 |
| 包间隔 | **1.136 ms** | 4 ms 周期剩余 |
| 线路占用率 | **71.6%** | 余量不多 |
| 连续流速率 | 8250 B/s | |

**可用的富余**：
- 固件有 `OPENBCI_BAUD_RATE_MIN_NO_AVG = 200000`，**关闭平均时可跑 200 kbaud**，占用率降到 41%。若 8 通道不够用或想加时间戳，可以走这个模式。
- **Cyton+Daisy 16 通道会做平均**（奇偶样本交替发送两个 ADS1299 的相邻样本均值），**不是原始数据**，做算法时要注意。

---

## 6. PL 侧实现规格

### 6.1 模块划分

```
uart_rx.v        异步串口接收 → 字节流
   │  byte[7:0], byte_valid
   ▼
pkt_parser.v     33 字节包同步/校验/拆包 → 样本
   │  sample_valid, ch[0..7][23:0], seq, pkt_type, err_flags
   ▼
（下游：prefilter → csp_project → ...）
   │
   └─► axis_pack.v  ──► AXI DMA ──► PS（仅用于显示/记录）
```

**设计原则**：`pkt_parser` 的输出直接进算法链，**不经过 PS**。给 PS 的那一路是"旁路监控"，不影响主通路延迟。

### 6.2 `uart_rx` 关键参数

| 参数 | 值 | 计算 |
|---|---|---|
| 系统时钟 | 100 MHz | |
| 位周期 | **868** 时钟 | 100e6 / 115200 = 868.06 → 取 868，波特率误差 **+0.006%** |
| 半位周期 | 434 | 用于采样点对齐 |
| 采样位置 | 每位中点 | 起始位下降沿后延时 1.5 位周期开始采 |

**结构**：

```
IDLE  : 检测 rx 下降沿 → 延时半位 → 确认仍为低 → 进 DATA
DATA  : 每 868 周期采一位，LSB 先，共 8 位
STOP  : 等 868 周期，检查 stop 位为高
        合法 → byte_valid = 1
        非法（帧错误）→ 计数，丢弃
```

- 输入先过**两级同步器**（异步信号）
- 建议加**数字毛刺滤波**（连续 3 个采样一致才认边沿），防止隔离器/长线毛刺
- 输出：`byte[7:0]`, `byte_valid`, `frame_err`

### 6.3 `pkt_parser` 状态机

```
S_IDLE : byte_valid && byte==8'h41 → idx<=0, buf[idx]<=byte, 进 S_RECV
S_RECV : byte_valid → idx<=idx+1, buf[idx]<=byte
         idx==32 时（收到第 33 字节）：
             校验 (byte & 8'hF0) == 8'hC0
               合法 → 输出样本，pkt_cnt++，回 S_IDLE
               非法 → footer_err++，回 S_IDLE
         字节间隔超时（> 2 字节时间 = 174 µs）→ sync_err++，回 S_IDLE
```

**丢包检测**：`seq_new != (seq_prev + 1) & 0xFF` → `drop_cnt += (seq_new - seq_prev - 1) & 0xFF`

⚠️ 序号只有 1 字节，**丢包超过 255 个样本时无法识别**。按官方口径，丢包率 > 1% 该记录即不可用。

**失步恢复**：超时机制是关键。因为包头 `0x41` 也可能出现在数据里，**不能只靠搜同步字**，必须结合"33 字节定长 + 包尾校验 + 字节间隔超时"三重判断。

### 6.4 输出接口（与算法侧的交接点）

```verilog
// 内部并行总线：一个采样点一拍
output             sample_valid;   // 高有效一拍
output [23:0]      ch_data [0:7];  // 8 通道，24 位有符号
output [7:0]       sample_seq;     // Cyton 采样序号
output [3:0]       pkt_type;       // 由包尾低 4 位给出
output [31:0]      err_flags;      // 见 §6.5
```

对 PS 的 AXI4-Stream 旁路：

```
TDATA = 8 通道 × 32 位（符号扩展）= 256 位
TVALID/TREADY 握手；每包 1 拍；TLAST 置 1
可选 TUSER = {pkt_type, sample_seq}
```

### 6.5 错误计数器

| 位 | 名称 | 含义 |
|---|---|---|
| [7:0] | `footer_err` | 包尾校验失败次数 |
| [15:8] | `sync_err` | 字节间隔超时次数 |
| [23:16] | `drop_cnt` | 累计丢样本数（由序号推算） |
| [31:24] | `frame_err` | UART 帧错误（stop 位非法） |

---

## 7. PS 侧参考实现（P2 阶段用）

### 7.1 最简版本（PYNQ / Linux）

```python
import serial, struct
ser = serial.Serial('/dev/ttyPS0', 115200, timeout=0.1)   # 或 /dev/ttyUSBx
ser.write(b'v'); ser.read_until(b'$$$')                   # 复位
ser.write(b'b')                                           # 开始推流
while True:
    b = ser.read(33)
    if len(b) < 33 or b[0] != 0x41:
        continue
    if (b[32] & 0xF0) != 0xC0:
        continue
    seq = b[1]
    ch = []
    for c in range(8):
        v = (b[2+3*c] << 16) | (b[3+3*c] << 8) | b[4+3*c]
        if v & 0x800000: v -= 1 << 24          # 符号扩展
        ch.append(v * 0.02235)                 # → µV
```

### 7.2 必须做的优化

1. **串口用阻塞读 + 定长 33**，不要 `readline`
2. **降低 FTDI/串口缓冲延迟**（如果经 USB dongle）：
   `echo 1 | sudo tee /sys/bus/usb-serial/devices/ttyUSB0/latency_timer`
3. **PS 端进程设实时优先级**，避免被调度抢占
4. **把解析结果按批（20–50 ms）经 AXI-DMA 送 PL**，不要逐样本

---

## 8. 交接接口汇总（一页速查）

| 层次 | 交接内容 | 规格 |
|---|---|---|
| **物理** | 3 根线 + 隔离 | 3V3 TTL，115200 8N1，共地 |
| **链路** | 33 字节包 | `0x41` … `0xC0\|type` |
| **PL 输入** | `uart_rx` 引脚 | 异步，需两级同步 + 滤波 |
| **PL 内部** | `sample_valid` + `ch[8][24]` | 一拍一个样本 |
| **PL → PS** | AXI4-Stream 256 位 | 仅监控，不影响主通路 |
| **PS → PL 控制** | AXI-Lite 寄存器 | 见下表 |
| **错误** | `err_flags[31:0]` | 四类计数 |

**AXI-Lite 寄存器草案**

| 偏移 | 名称 | R/W | 说明 |
|---|---|---|---|
| `0x00` | `LINK_CTRL` | RW | [0] enable [1] soft_reset [2] loopback_test |
| `0x04` | `LINK_STATUS` | RO | [0] locked, [15:8] fifo_level, [31:16] reserved |
| `0x08` | `PKT_CNT` | RO | 累计收到合法包数 |
| `0x0C` | `LAST_SEQ` | RO | 最后收到的采样序号 |
| `0x10` | `BAUD_DIV` | RW | 位周期分频，默认 **868** |
| `0x14` | `ERR_FLAGS` | RO | 见 §6.5 |

---

## 9. 验收测试（按顺序做，每步通过再进下一步）

| # | 测试 | 方法 | 通过判据 |
|---|---|---|---|
| T1 | **PL 环回** | `UART_TX` 直连 `UART_RX`，发已知字节串 | 收到的字节完全一致 |
| T2 | **假数据注入** | Python 造 33 字节包（含 `0x41`/`0xC0`），从 PS 的 TX 发给 PL | `PKT_CNT` 递增，`ch_data` 与注入值一致 |
| T3 | **失步恢复** | 故意在流中间插入垃圾字节 | `sync_err` 增加，但能自动恢复，不永久卡死 |
| T4 | **真实 Cyton 接入** | 接上 D11/D12，发 `/1` 进 debug，发 `b` | 稳定收到包；用示波器/逻辑分析仪核对首字节确为 `0x41` |
| T5 | **PL vs PS 对拍** | 同一份流，PL 解析结果与 PS 解析结果比对 | **逐样本完全一致** |
| T6 | **长时间烤机** | 连续跑 30 min | 丢包率 < 0.1%，无死锁 |

**T2 的测试向量生成脚本要一并交付**——它是 T5 对拍的基础，也是将来换采集前端时的回归测试集。

---

## 10. 交付清单

| 类别 | 交付物 | 负责侧 |
|---|---|---|
| RTL | `uart_rx.v`, `pkt_parser.v`, `axis_pack.v`, `link_top.v` | PL |
| 验证 | `tb_link.v`（含 T1/T2/T3 用例） | PL |
| 约束 | XDC 片段（引脚 + 时序例外） | PL |
| 工具 | `gen_test_vector.py`（造假包 + 生成期望输出） | 共用 |
| 参考实现 | `ps_uart_ref.py`（P2 阶段的解析器，兼对拍金标准） | PS |
| 文档 | 本文档 + 寄存器映射更新到 [01 号文档](01_fixed_point_interface.md) | 共用 |

---

## 11. 三个必须上板确认的未知项

1. **D11/D12 到底有没有在输出？** 有用户报告新固件直接发 `/1` 就能用；但固件注释说要改 `Board_Defs.h` 第 311/313 行 (`_SER1_TX_PIN`/`_SER1_RX_PIN`) 到 11/12 并重烧。**用逻辑分析仪抓一下最快。**
2. **D12 能不能收命令？** 若可以，就能彻底拔掉 dongle 做到全有线；若不行，命令走无线（dongle 插 PS 或 PC）也完全可以——命令是低频的，不影响数据通路。
3. **板子固件版本是否统一？** 论坛有一例"第二字节恒为 `0x41`"的怪象，根因是 PIC / Device / Host 三者固件版本不匹配。**上板前先统一刷到同一版本**，否则会浪费很多时间在错误的排查方向上。

---

*本文档中 Cyton 侧的所有参数（`0x41`/`0xC0`/115200/3V3/33 字节）均取自固件源码
`OpenBCI/OpenBCI_Cyton_Library`（已存 `参考资料/OpenBCI_32bit_Library.cpp`），
不是从二手文档抄的。修改本文档请同步更新 [01 号文档](01_fixed_point_interface.md) 的寄存器映射。*
