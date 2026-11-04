# FPGA EEG motor imagery recognition prototype
## 2026年全国大学生嵌入式大赛参赛作品--AMD自选组初级赛道
报名作品：基于FPGA脑电辅助手康复设备。当前完成识别与显示，辅助医生进行手部抓握康复。

## 硬件与设备信息
Alientek Navigator Zynq7020, xc7z020clg400-2; PetaLinux2020.2 ARMv7 hard-float. Vivado2025.2; actual PL core80MHz; PS FCLK100MHz. OpenBCI250Hz; ST7789 320x240; EC11 rotary encoder.
实时输入C3=CH3、C4=CH4，PS执行50Hz陷波，PL三个输入槽位为C3、0、C4。

## 目录说明
`src/`（设计源码：`pl` RTL、`ps_app` PS 软件）
`sim/`（仿真与验证：`rtl`、`reference`、`results`、`host_tools` 主机端工具）
`build/`（构建脚本与综合实现报告）、`board/`（上板工程、部署与运行脚本）
`data/`（测试数据与参考结果）、`skill/`（技能包）、`report/`（设计报告与协作记录，设计文档在 `report/design/`）。
`training/`（训练/分析程序）
`params/`（定点参数），

## 开源协议
本仓库按 MIT 许可开源，见根目录 LICENSE。
