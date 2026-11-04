# 11 · 上板操作指南（PL 下载 + PS 操作）

> 配套：[09 PL/PS 设计文档](09_pl_ps_design.md) · [10 调试报告](10_debug_report.md) · [04 串口接入交接文档](04_uart_handoff.md)
> 代码：`zynq/pl/`（RTL + 约束 + 综合脚本）· `zynq/ps/`（PS C 代码）
> 版本 v1.0

---

## 0. ⚠️ 先读这一节：现在**还不能直接上板**

本指南是完整的操作步骤，但有几件事**必须先做完**，否则会卡住：

| # | 缺什么 | 影响 | 怎么办 |
|---|---|---|---|
| 1 | **Block Design 还没建过** | `zynq/ps/eeg_regs.h` 里的 `EEG_AXI_BASE` 是**占位值** `0x43C00000` | 跑 `build_bd.tcl`，把打印出的真实地址填回去 |
| 2 | **引脚未核对** | 厂商明说"丝印出错已更正"，PYNQ-Z2 的引脚来自公开表 | 用万用表/示波器确认，见 §2.3 |
| 3 | **数字隔离器还没接** | ⚠️ **安全风险**，见 §2.2 | ADuM1201 / Si8620 |
| 4 | **PS 端代码没在真板子上跑过** | 只做过交叉编译验证 | 按 §4 逐步来 |

**第 1 条是最容易卡住的**——地址错了会表现为"AXI 读回全 1"，见 §6.2。

---

## 1. 全景：一份代码，两块板

```
PC ──(Vivado)──► 比特流 ──┐
                          ├──► ZYNQ7020-484（当前验证板）
PC ──(PetaLinux SDK)──► PS 可执行文件 ──┘
                          └──► PYNQ-Z2（后续演示板，只换 XDC + 重综合）
```

**换板子要改的只有三处**（这是双板兼容设计的直接收益）：

| 位置 | ZYNQ7020-484 | PYNQ-Z2 |
|---|---|---|
| `build_bd.tcl` 的 `PART` | `xc7z020clg484-2` | `xc7z020clg400-1` |
| `add_files` 的 XDC | `eeg_csp_svm_zynq484.xdc` | `eeg_csp_svm_pynqz2.xdc` |
| `eeg_regs.h` 的 `EEG_AXI_BASE` | Vivado 分配 | Vivado 分配 |

RTL、仿真、PS 代码**一行都不用动**。

---

## 2. 硬件准备

### 2.1 需要的器材

| 器材 | 说明 |
|---|---|
| ZYNQ7020-484 核心板 + 底板 | 当前验证板 |
| OpenBCI Cyton + Dongle | 8 通道 ADS1299；**只取前 3 通道**（C3/Cz/C4） |
| **数字隔离器模块** | ADuM1201 / Si8620，**必须**，见 §2.2 |
| microSD 卡（≥8 GB） | 装 PetaLinux 镜像 |
| USB 线 ×2 | 一根 JTAG（下载 PL），一根 PS UART（看控制台） |
| 杜邦线若干 | 接串口，尽量短（<20 cm） |

电池：Cyton 用**电池供电**，不要接 USB。

### 2.2 ⚠️ 安全：隔离器不能省

```
Cyton D11(TX) ──►│ ADuM1201 │──► FPGA uart_rx   (E15)
Cyton D12(RX) ◄──│ 或 Si8620│◄── FPGA uart_tx   (E16)
Cyton GND     ──►│          │──► FPGA GND
```

**为什么必须**：Cyton 是锂电池供电、与被试隔离的。USB 供电的 FPGA 板一旦和 Cyton 共地，
就在被试身上建立了**到市电地的通路**——一旦探头脱落碰到别的东西，电流会经过被试。

这不是"最好加上"，是**没有就不要接人**。调试阶段可以先用信号发生器/环回代替。

### 2.3 引脚核对（上板前必做）

```powershell
# 打开约束文件
notepad zynq\pl\constr\eeg_csp_svm_zynq484.xdc
```

当前分配：

| 信号 | 引脚 | 说明 |
|---|---|---|
| `uart_rx` | **E15** | BANK35，来自隔离器输出 |
| `uart_tx` | **E16** | BANK35，去隔离器输入 |
| `led_pkt` | D18（已注释） | 收到合法包的心跳 |
| `led_label` | C19（已注释） | 决策标签 |

**核对方法**（三选一，至少做一样）：
1. 对照 `ZYNQ7020-484核心板/00.使用前必看！！！/ZYNQ7020补充资料.zip` 里**更正后的**丝印图
2. 用万用表蜂鸣档，从核心板金手指量到排针
3. 先烧一个最简单的"按键点灯"比特流，确认引脚能通

> 厂商明确写了"由于丝印出错，现更新了丝印位号"，**不要凭板子上的字判断**。

---

## 3. PL 端：从 RTL 到比特流

### 3.1 建 Block Design（★ 首次必做，决定 AXI 地址）

```powershell
cd H:\FPGA2026QS\zynq\pl\synth

# 先确保 LUT 文件在（build_bd.tcl 会复制到工程里）
Copy-Item ..\sim\log2_lut.hex . -Force -ErrorAction SilentlyContinue

# 跑 BD 脚本（含综合实现出比特流，20~40 分钟）
& 'D:\Xilinx\2025.1\Vivado\bin\vivado.bat' -mode batch -source build_bd.tcl `
    -nojournal -log vivado_bd.log
```

脚本会自动：
1. 建 `eeg_system` BD：PS7 + SmartConnect + 我们的 `eeg_csp_svm_top`
2. PS7 配成 `FCLK_CLK0 = 100 MHz`、使能 `M_AXI_GP0`
3. `assign_bd_address` 分配地址
4. **打印 AXI 基地址** ← 记下来
5. 综合 → 实现 → 出比特流
6. 导出 `eeg_system.xsa`（给 PS 用）

**看到这两行说明成功**：
```
  ★★ AXI 基地址 = 0x43C00000
BD_DONE_OK
```

### 3.2 ★ 把真实地址填回 PS 代码

```c
// zynq/ps/eeg_regs.h
#define EEG_AXI_BASE      0x43C00000u   // ← 改成 build_bd.tcl 打印的值
```

**不改会出现什么**：PS 读回 `0xFFFFFFFF`，程序在 `eeg_axil_open` 后立刻退出（见 §6.2）。

### 3.3 产物在哪

| 文件 | 路径 | 用途 |
|---|---|---|
| 比特流 | `eeg_bd/eeg_bd.runs/impl_1/eeg_system_wrapper.bit` | 下载 PL |
| 硬件平台 | `zynq/pl/synth/eeg_system.xsa` | 给 Vitis / PetaLinux |
| 地址表 | `zynq/pl/synth/report_bd/address_map.rpt` | 核对地址 |
| 时序报告 | `eeg_bd/eeg_bd.runs/impl_1/*_timing_summary_routed.rpt` | 确认 WNS > 0 |

### 3.4 下载 PL 的两种方式

#### 方式 A：JTAG 在线下载（**开发调试用这个**）

适合：改一版 RTL 就想马上看效果。

```
1. 用 JTAG 线连上板子，给板子上电
2. Vivado → Open Hardware Manager → Open Target → Auto Connect
3. 右键 xc7z020_1 → Program Device
4. Bitstream 选 eeg_system_wrapper.bit
5. Program
```

也可以命令行：
```tcl
open_hw_manager
connect_hw_server
open_hw_target
set_property PROGRAM.FILE {eeg_system_wrapper.bit} [current_hw_device]
program_hw_devices [current_hw_device]
```

⚠️ **JTAG 下载的 PL 配置在断电后丢失**。而且如果 PS 已经在跑 Linux，
重新下载 PL 会让 AXI 从机复位——**PS 侧程序必须重新初始化**。

#### 方式 B：打进 BOOT.BIN，随系统启动（**部署/演示用这个**）

这样上电后 PL 自动配置好，PS 起来就能用。

```
BOOT.BIN = FSBL + bitstream + u-boot.elf
image.ub = Linux 内核 + 设备树 + rootfs
```

打包（在 PetaLinux 工程里）：
```bash
# 把比特流和 bd 文件放进 PetaLinux 工程
cp eeg_system_wrapper.bit  <petalinux-proj>/project-spec/hw-description/
cp eeg_system.xsa          <petalinux-proj>/project-spec/hw-description/

petalinux-config --get-hw-description=<petalinux-proj>/project-spec/hw-description/
#   → Subsystem AUTO Hardware Settings → FPGA Manager → 选 "Enable"
petalinux-build
petalinux-package --boot --fsbl <fsbl.elf> --fpga eeg_system_wrapper.bit --u-boot
```

把生成的 `BOOT.BIN` + `image.ub` 拷到 SD 卡 FAT 分区，插卡上电即可。

> **厂商出厂固件**（`鹿小班-ZYNQ-484出厂测试固件.zip`）里已经有能启动的
> `BOOT.BIN` + `image.ub`。**建议先用它验证板子本身是好的**，
> 再换成自己的——否则分不清是板子问题还是自己的问题。

### 3.5 系统启动后在 Linux 里加载 PL（第三种方式）

如果没打进 BOOT.BIN，Linux 起来后也能加载：

```bash
# 把 .bit 拷到板子上
scp eeg_system_wrapper.bit root@<板子IP>:/lib/firmware/

# 方式 1：老接口（Zynq-7000 常用）
cat /lib/firmware/eeg_system_wrapper.bit > /dev/xdevcfg

# 方式 2：新接口（fpga_manager）
echo eeg_system_wrapper.bit > /sys/class/fpga_manager/fpga0/firmware
cat /sys/class/fpga_manager/fpga0/state     # 应显示 operating
```

---

## 4. PS 端：编译、部署、运行

### 4.1 编译（两种方式）

#### 方式 A：PC 上交叉编译（推荐）

```powershell
cd H:\FPGA2026QS\zynq\ps

# 用 Xilinx 自带的 aarch64 工具链
$gcc = 'D:\Xilinx\2025.1\gnu\aarch64\nt\aarch64-linux\bin\aarch64-linux-gnu-gcc.exe'

& $gcc -O2 -Wall -Wextra -std=c11 -DEE_BOARD=EEG_BOARD_ZYNQ484 `
    -o eeg_ps eeg_ps.c axil.c ea.c -lm
```

产物 `eeg_ps` 是 aarch64 ELF（约 74 KB）。

#### 方式 B：板子上直接编译

PetaLinux 自带 gcc，把 `zynq/ps/` 整个拷过去：
```bash
make CROSS= BOARD=EEG_BOARD_ZYNQ484
```

### 4.2 部署

```bash
# 板子上
mkdir -p /home/xilinx/eeg
# 从 PC 拷过去
scp zynq/ps/eeg_ps            root@<板子IP>:/home/xilinx/eeg/
scp params/csp_svm_params.json root@<板子IP>:/home/xilinx/eeg/
```

### 4.3 运行

#### 第 0 步：确认 AXI 通了（**最容易出错的一步**）

```bash
# 需要 root（要访问 /dev/mem）
sudo -i

# 手工读一次 STATUS 寄存器（偏移 0x04）
devmem 0x43C00004 32
```

| 读回值 | 含义 | 下一步 |
|---|---|---|
| `0x00000000` | ✓ AXI 通了（参数还没下发） | 继续 |
| `0xFFFFFFFF` | ✗ 基地址不对 或 PL 没配置 | 见 §6.2 |
| 卡住/报错 | ✗ `/dev/mem` 权限或地址未映射 | 见 §6.2 |

#### 第 1 步：无硬件自检（先确认数学没问题）

```bash
./eeg_ps --sim-calib --calib 20
```
会跑一遍 EA 折叠流程并打印 `|W_eff|max` 和量化余量。**不碰硬件**，用来排除算法问题。

#### 第 2 步：UART 环回自检（**不接 Cyton 就能验证串口链路**）

把 `uart_tx`（E16）用杜邦线短接到 `uart_rx`（E15）：
```bash
./eeg_ps --loopback
```
（若该选项未实现，可用示波器看 `uart_tx` 是否有波形。）

#### 第 3 步：接 Cyton，跑真实的

```bash
# ① 校准：20 个试次（约 1 分钟），被试保持安静
sudo ./eeg_ps --calib 20 --weights w_unfolded.txt --out run.csv

# ② 观察输出
#   每 250 个决策打印一行：丢包数 / 同步错 / PL 周期数
```

参数说明：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--calib N` | 20 | EA 校准试次数。**5 个不够**（实测负增益），10 个才稳定 |
| `--weights F` | 占位 | 未折叠的 CSP 权重 `W̃`（由 MATLAB 训练导出） |
| `--out F` | 无 | 决策序列 CSV（含时间戳与决策值） |
| `--sim-calib` | 关 | 用合成数据代替真实采集，做无硬件自检 |

### 4.4 校准数据从哪来

⚠️ **这是当前架构里唯一还没打通的环节。**

实时数据通路在 PL 内闭环，PS 拿不到逐样本 EEG。校准需要原始数据，只能走**专用旁路**：
- PL 在校准模式下把滤波前的样本写进 RAW_DUMP BRAM
- PS 在试次结束后一次性读出来

**这个 `raw_dump` 模块还没写**（见 `zynq/README.md` 的待办清单）。
所以现在只能用 `--sim-calib` 验证算法与参数下发流程。

**临时替代方案**：把 `W̃` 和 EA 白化矩阵在 PC 上用 MATLAB 算好后，
直接生成 `W_eff` 的 Q1.14 整数值写进参数文件，PS 只负责下发（不做在线校准）。

---

## 5. 上电与验证顺序（**按这个顺序排查，不要跳步**）

```
① 板子单独上电，串口能看到 Linux 启动日志
      ↓ 看不到 → 板子/镜像问题，与我们的设计无关
② 加载 PL，devmem 读 0x43C00004 得到 0x00000000
      ↓ 读到 0xFFFFFFFF → AXI 基地址或 PL 未配置（§6.2）
③ UART 环回自检
      ↓ 失败 → 引脚错了或波特率不对（§6.3）
④ 接隔离器 + Cyton（先不接人！），看 led_pkt 是否闪烁
      ↓ 不闪 → 包解析有问题（§6.4）
⑤ 跑 --sim-calib 确认 EA 数学
      ↓ 失败 → PS 代码问题，与 PL 无关
⑥ 接被试，跑 --calib 20，看决策输出
```

**每一步都要单独确认**。跳过第 ② 步直接接 Cyton，出问题时无法区分是 AXI 还是串口。

### 监控手段

| 手段 | 在哪看 |
|---|---|
| 丢包 / 同步错计数 | PS 每 250 个决策打印一次；也读 `ERR_FLAGS`（0x28） |
| `led_pkt` 心跳 | 收到合法包就翻转；不闪说明没数据 |
| `led_label` | 直接显示当前决策 |
| 决策序列 | `run.csv` |
| PL 内部信号 | Vivado ILA（把 `sample_valid`、`z`、`f_sel` 加进 debug 核） |

---

## 6. 故障排查

### 6.1 PS 起不来

| 现象 | 原因 | 处理 |
|---|---|---|
| 串口无输出 | SD 卡没插好 / BOOT.BIN 不对 | 先用**厂商出厂固件**验证板子 |
| 卡在 "Uncompressing Linux" | image.ub 与内核不匹配 | 重新 `petalinux-build` |
| 起得来但没网 | 网络配置 | 不影响本项目（串口通路不依赖网络） |

### 6.2 AXI 读回 0xFFFFFFFF

**按可能性排序**：

| # | 原因 | 验证方法 |
|---|---|---|
| 1 | **基地址不对**（最常见） | 看 `report_bd/address_map.rpt`，与 `eeg_regs.h` 对照 |
| 2 | PL 没配置 | `cat /sys/class/fpga_manager/fpga0/state`，应为 `operating` |
| 3 | AXI 互连没接上 | 在 Vivado 里 `validate_bd_design` 要无错 |
| 4 | PS 的 `M_AXI_GP0` 没使能 | BD 里看 PS7 配置 |
| 5 | 时钟没到 PL | ILA 抓 `clk_in` 有没有翻转 |

> **调试技巧**：把地址按 4 字节步进扫一遍（`devmem 0x43C00000`、`0x43C00004`…），
> 如果全是 `0xFFFFFFFF`，基本可以确定是 PL 没配置或地址段完全不对。

### 6.3 收不到串口数据（`led_pkt` 不闪）

| # | 原因 | 验证方法 |
|---|---|---|
| 1 | **引脚接错** | 万用表量通断；先做环回 |
| 2 | RX/TX 接反 | 交换 E15/E16 试 |
| 3 | 没共地 | 隔离器两侧都要接 GND |
| 4 | **Cyton 没开始发送** | Cyton 要收到 `'b'` 命令才开始流数据；用 OpenBCI GUI 确认 |
| 5 | 波特率不对 | 必须是 115200 8N1；PL 时钟必须是 PLL 输出的 95 MHz |
| 6 | 隔离器方向接反 | ADuM1201 有方向性，看数据手册 |

**注意**：PL 的 `uart_tx` 目前是**常高（空闲）**，还没实现向 Cyton 发 `'b'` 命令。
所以要么用 OpenBCI GUI 先启动数据流，要么补上命令通道。

### 6.4 收到包但决策不对

| 现象 | 原因 |
|---|---|
| `cnt_pkt` 增长但 `drop` 也在涨 | 丢包；检查线缆长度与隔离器 |
| 决策值恒等于 `svm_b` | 特征全是 0 → 检查 `W_eff` 是否下发成功 |
| 决策值抖动很大 | 窗口没填满（前 626 个样本）；或校准没做 |
| 精度远低于 80% | `W_eff` 没做**每带归一化**（见 §6.5） |

### 6.5 ⚠️ 精度异常的头号嫌疑：CSP 权重归一化

EA 折叠会把 `|W_eff|max` 抬到 **19~20**（8 通道）或 3.81（3 通道）。
而 PL 侧的格式是 **Q1.14 / 16 bit（范围 ±2.0）**。

**必须先归一化再量化**——依据是特征定义对整体缩放免疫：

$$f_i = \log\frac{\text{var}_i}{\sum_j \text{var}_j}$$

`PS 侧 ea.c 的 ea_quantize_w()` 已经做了这件事（缩放到峰值 0.875）。
**如果换成别的方式生成权重，必须自己做这一步**，否则会静默截断。

诊断方法：读回参数后检查 `|W|max` 是否 ≤ 32767（Q1.14 上限）。

---

## 7. 快速命令速查

```powershell
# ── PL：建 BD + 出比特流（首次或改了 RTL）──
cd H:\FPGA2026QS\zynq\pl\synth
& 'D:\Xilinx\2025.1\Vivado\bin\vivado.bat' -mode batch -source build_bd.tcl -nojournal -log vivado_bd.log

# ── PL：只跑综合实现（不建 BD，快速看时序/资源）──
& 'D:\Xilinx\2025.1\Vivado\bin\vivado.bat' -mode batch -source synth_eeg.tcl -nojournal -log vivado_synth.log
python parse_reports.py

# ── PL：改 RTL 后先跑仿真（秒级，必做）──
cd H:\FPGA2026QS\zynq\pl\sim
& H:\FPGA2026QS\.venv\Scripts\python.exe verify_fullflow.py

# ── PS：交叉编译 ──
cd H:\FPGA2026QS\zynq\ps
& 'D:\Xilinx\2025.1\gnu\aarch64\nt\aarch64-linux\bin\aarch64-linux-gnu-gcc.exe' `
    -O2 -Wall -Wextra -std=c11 -DEE_BOARD=EEG_BOARD_ZYNQ484 -o eeg_ps eeg_ps.c axil.c ea.c -lm

# ── PS：验证 EA 数学（不需要硬件）──
& H:\FPGA2026QS\.venv\Scripts\python.exe verify_ea_math.py
```

```bash
# ── 板子上 ──
sudo -i
devmem 0x43C00004 32                      # STATUS，应非 0xFFFFFFFF
cat /sys/class/fpga_manager/fpga0/state   # operating
./eeg_ps --sim-calib --calib 20           # 无硬件自检
./eeg_ps --calib 20 --weights w.txt --out run.csv
```

---

## 8. 换到 PYNQ-Z2 演示

1. `build_bd.tcl` 改 `set PART xc7z020clg400-1`
2. `build_bd.tcl` 里 XDC 换成 `eeg_csp_svm_pynqz2.xdc`
3. **PYNQ 的 PL 时钟也走 PS 的 `FCLK_CLK0`，不要用板载 125 MHz 晶振**
   （拿板载晶振会让 PLL 输入变成 125 MHz，输出变成 118.75 MHz，波特率全错）
4. 跑 `make BOARD=EEG_BOARD_PYNQZ2` 重编 PS 代码（只改了 AXI 基地址）
5. PYNQ 上也可以用 Python 访问，但**推荐继续用同一份 C 代码**——
   这样才是"同一套代码在两块板上跑"，而不是两套实现

> ⚠️ **PYNQ 镜像在 ZYNQ7020-484 上跑不了**（DDR/时钟/设备树都不同）。
> 反过来也一样。两块板**各自的启动镜像不同，但 PL 比特流与 PS 应用代码同源**。
