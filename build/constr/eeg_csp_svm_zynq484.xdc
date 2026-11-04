#=============================================================================
# eeg_csp_svm_zynq484.xdc —— ZYNQ7020-484 核心板引脚约束
#=============================================================================
# 芯片：xc7z020clg484-2
# 依据：ZYNQ7020-484核心板/00.使用前必看！！！/ZYNQ7020补充资料.zip
#       → ZYNQ7020-484引脚丝印说明.docx（厂商更正后的丝印图）
#
# ★★ 使用前必须核对丝印！
#   厂商明确说明"由于丝印出错，现更新了丝印位号"，本文件按**更正后**的图写。
#   上板前用万用表/示波器确认：E15 是 BANK35 的哪一号脚。
#
# 与 PYNQ-Z2 的一致性要求（见设计文档 §0）：
#   - 端口名完全相同（uart_rx / uart_tx / clk_in / rst_n_ext）
#   - PS 只提供 FCLK_CLK0 = 100 MHz；PL 内部由 clk_pll（MMCM）变成 95 MHz
#   - 电平标准都是 LVCMOS33（3.3V，与 Cyton 的 D11/D12 匹配）
#   → 换板子只需换这一个文件，RTL 与 PS 代码一行不动
#=============================================================================

#---------------------------------------------------------------------------
# 时钟与复位
#---------------------------------------------------------------------------
# ★ 时钟由 PS 的 FCLK_CLK0 提供，在 Block Design 里设置成 100 MHz。
#   分频系数（868）与超时阈值（17360）都按 100 MHz 写死，**不能改频率**。
#   若误用板载晶振（PYNQ-Z2 是 H16=125MHz），UART 波特率会偏 25%，收不到数据。
# 这里只对 PS 送出来的时钟做时序约束（端口在 BD 内部，不在顶层端口列表）。

# 复位：用 PL 侧的上电复位或 PS 的 FCLK_RESET0_N，不占外部引脚。
# 若需要手动复位按钮，解除下面注释并接到 BANK35 的某个空闲脚。
# set_property -dict { PACKAGE_PIN E15 IOSTANDARD LVCMOS33 } [get_ports rst_n_btn]

#---------------------------------------------------------------------------
# Cyton 串口（BANK35，3.3V）
#   Cyton D11 (TX, PIC32 Serial1 TX) → FPGA uart_rx
#   FPGA uart_tx → Cyton D12 (RX, PIC32 Serial1 RX)   ← 需实测固件是否接受
#
#   ★ 中间必须串数字隔离器（ADuM1201 / Si8620）！
#     Cyton 是锂电池供电、与被试隔离的；USB 供电的 FPGA 板一旦共地，
#     就在被试身上建立了到市电地的通路，破坏隔离。
#---------------------------------------------------------------------------
set_property -dict { PACKAGE_PIN E15  IOSTANDARD LVCMOS33 } [get_ports uart_rx]
set_property -dict { PACKAGE_PIN E16  IOSTANDARD LVCMOS33 } [get_ports uart_tx]

# 异步输入必须做时序例外，否则 Vivado 会按同步路径分析并报大量违例
set_false_path -from [get_ports uart_rx]
set_false_path -from [get_ports rst_n_ext]
# 输出是慢速信号（86.8 µs/位），不需要输出延迟约束
set_property DRIVE 8 [get_ports uart_tx]

#---------------------------------------------------------------------------
# 调试/演示用 LED（可选，BANK35 空闲脚）
#   led[0] = 收到合法包的心跳
#   led[1] = 决策输出（左/右手）
#---------------------------------------------------------------------------
# set_property -dict { PACKAGE_PIN D18 IOSTANDARD LVCMOS33 } [get_ports {led[0]}]
# set_property -dict { PACKAGE_PIN C19 IOSTANDARD LVCMOS33 } [get_ports {led[1]}]
