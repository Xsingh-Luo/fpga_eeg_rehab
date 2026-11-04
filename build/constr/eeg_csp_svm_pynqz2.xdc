#=============================================================================
# eeg_csp_svm_pynqz2.xdc —— PYNQ-Z2 引脚约束（演示备用）
#=============================================================================
# 芯片：xc7z020clg400-1
# 依据：PYNQ-Z2 PMOD 引脚（社区整理的官方 pinout）
#       https://wiki.dcae.pub.ro/index.php?title=Pynq-Z2_-_Pinout
#
# ★★ 上板前**必须**用官方 Master XDC 核对下面这些引脚号！
#   本仓库没有 PYNQ-Z2 的官方板级文件，这里的号来自公开 pinout 表。
#   引脚写错可能损伤板子，核对成本很低。
#
# ★★ 与 ZYNQ7020-484 完全一致的三条约定：
#   1. 端口名相同：uart_rx / uart_tx / clk_in / rst_n_ext
#   2. PL 时钟用 PS 的 FCLK_CLK0 = 100 MHz（**不要用板载 H16 的 125 MHz**）
#   3. 电平 LVCMOS33
#
# ★ 特别提醒：PYNQ-Z2 的 PL 有一个 125 MHz 晶振（H16）。
#   若为了省事拿它当时钟，UART 分频会按 868 走，实际波特率变成 144000，
#   Cyton 那边收不到正确数据 —— 现象是"完全没数据"而不是"数据错"，
#   很容易误判成接线问题。**务必用 FCLK_CLK0。**
#=============================================================================

#---------------------------------------------------------------------------
# Cyton 串口 → PMODA（上排）
#   Cyton D11 (TX) → FPGA uart_rx  → PMODA 第 1 脚
#   FPGA uart_tx   → Cyton D12(RX) → PMODA 第 2 脚
#   GND 必须共地（经隔离器）
#---------------------------------------------------------------------------
set_property -dict { PACKAGE_PIN Y18  IOSTANDARD LVCMOS33 } [get_ports uart_rx]
set_property -dict { PACKAGE_PIN Y19  IOSTANDARD LVCMOS33 } [get_ports uart_tx]

set_false_path -from [get_ports uart_rx]
set_false_path -from [get_ports rst_n_ext]
set_property DRIVE 8 [get_ports uart_tx]

#---------------------------------------------------------------------------
# 调试 LED（PYNQ-Z2 板载 4 个 LED）
#---------------------------------------------------------------------------
# set_property -dict { PACKAGE_PIN R14 IOSTANDARD LVCMOS33 } [get_ports {led[0]}]
# set_property -dict { PACKAGE_PIN P14 IOSTANDARD LVCMOS33 } [get_ports {led[1]}]
# set_property -dict { PACKAGE_PIN N16 IOSTANDARD LVCMOS33 } [get_ports {led[2]}]
# set_property -dict { PACKAGE_PIN M14 IOSTANDARD LVCMOS33 } [get_ports {led[3]}]

#---------------------------------------------------------------------------
# 板载时钟（仅备查，**本项目不使用**）
#   H16 = 125 MHz PL 晶振
#   若确实要用，必须把 RTL 参数 CLK_FREQ 改成 125_000_000 并重新算分频，
#   否则 UART 波特率错 25%。不推荐 —— 两块板行为就不一致了。
#---------------------------------------------------------------------------
