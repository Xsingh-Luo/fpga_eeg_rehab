# 本次新增内容说明

本仓库以 **2026-11-03 冻结候选包** `fpga_eeg_rehab`（172 个文件、`manifest.sha256` 覆盖其中 171 个）为基线，2026-11-04 从开发工作区补入此前未打包的**核心代码与设计文档**。基线文件保持逐字节不变；PL/PS 源码（`src/pl`、`src/ps_app`）与原候选包完全一致，未作任何改动。

## 1. 目录组织（对齐大赛推荐结构）

| 推荐目录 | 本仓库对应 | 说明 |
|---|---|---|
| `README.md` | `README.md` | 项目简介与复现入口 |
| `src/` | `src/` | `pl` 为 RTL，`ps_app` 为 PS 端软件（与原候选包逐字节一致） |
| `sim/` | `sim/` | `rtl` 测试平台、`reference` 参考模型、`results` 原始结果、`host_tools` 主机端联调工具 |
| `build/` | `build/` | 重建脚本、约束与综合/实现报告 |
| `board/` | `board/` | 自研位流、接线、部署与上板运行脚本 |
| `data/` | `data/` | 模型参数、20 试次回放与参考基线 |
| `skill/` | `skill/` | 可复用排查与验证经验 |
| `report/` | `report/` | 结果说明、AI 协作记录；`report/design` 为设计文档与框图 |
| — | `training/` | 原始训练/分析程序（包内脚本按 `ROOT/training` 引用，故保留在根） |
| — | `params/` | 定点参数与黄金激励（包内脚本按 `ROOT/params` 引用，故保留在根） |

仓库内目录名与文件名均为纯英文（小写字母、数字、下划线或连字符）；中文仅出现在文件正文中。

## 2. 新增内容

| 新增位置 | 来源（原工作区） | 内容与用途 |
|---|---|---|
| `sim/host_tools/` | `tools/` | 主机端联调工具链：Cyton 串口模拟器（CLI/GUI）、33 字节包解析器、真机采集、图形化测试台、左右手 MI 提示器、信号质检；含 744 包样本流 `sample_stream.bin/.csv`。解析器同时是 Zynq 端 C/Verilog 解析逻辑的参考实现。 |
| `params/` | `params/` | 最终定点参数与黄金激励：`csp_svm_params.json/.h`（3 子带 FBCSP + EA + 线性 SVM，BCI IV 2b subject 04）、`golden_vectors.mat`、`eog_variants.npy`。基线内 `training/`、`sim/reference/` 多个脚本按 `ROOT/params/csp_svm_params.json` 引用本目录。 |
| `report/design/` | `规划/` | 核心设计文档（01 定点契约、02 算法规格、03 算法教学、04 串口接入、05 移植交接、06 FBCSP 与优化、07 验证结果与最终配置、08 体系结构与数据流、09 PL/PS 设计、10 调试报告、11 上板操作指南、14 OpenBCI 实采范式）与 16 张系统/分析框图。 |
| `training/ablate_channels_2a.py` | `python/` | 通道消融实验（基线包内缺失的唯一训练脚本）。 |
| `training/matlab/{predict_record.m, train_c3c4_2ch.m, validate_pipeline.m, README.md}` | `matlab/` | 逐记录预测、C3/C4 两通道训练、流水线校验脚本及 MATLAB 侧说明（基线包内缺失）。 |
| `LICENSE` | 新增 | MIT 许可，覆盖项目原创代码；第三方组件另按原许可处理。 |
| `.gitattributes` | 仓库根 | 沿用主仓设置：默认 `* -text`，禁止自动行尾转换，二进制文件关闭 diff。 |

## 3. 完整性

- `manifest.sha256` 现覆盖 168 个基线文件；README、协作记录与技能包的哈希已随本次文档更新同步。原 `report/current_chat_excerpts.md`、`report/implementation_report.md`、`report/source_and_rights.md` 已按发布整理移除，清单中对应条目一并删除。在本目录运行 `python sim/check_release.py` 通过（输出 `manifest_files: 168`）。
- 新增文件**不在**冻结清单内。若需重做参赛提交 RAR，请先按同一最终版本重新生成 `manifest.sha256`，不能把更新后的仓库与旧 RAR 混用。

## 4. 有意未收录的内容

| 未收录 | 原因 |
|---|---|
| `zynq/ps/`、`zynq/pl/synth/`、`zynq/pl/sim/` 旧版 PL/PS 实现与构建/仿真脚本 | PL 与 PS 部分以 2026-11-03 冻结候选包为准（`src/pl`、`src/ps_app`、`build/rebuild_pl.tcl`），不并列另一套实现，避免混淆；原件仍保留在开发工作区。 |
| `data/raw/`、`record/1.mat`、`sessions/`、`matlab/data/subj*.mat` | 个人原始脑电与自采记录，不在公开范围。 |
| `third_party/`、`ZYNQ7020-484核心板/`、`参考资料/`、`NUECSDC_FPGA_Track/`、`RF_BCI/windows_tools/` | 厂商资料、第三方论文代码（含 GPL 参考工程）、OpenBCI GUI 安装包，不随本仓库再分发。 |
| `规划/参考对话/`、`规划/13_项目总结.md`、`版本管理提示词.md`、`资源清单.md` | 私人 AI 对话记录、参赛报告草稿与内部清单。 |
| 根目录 `01_/03_/05_*.md` | 与设计文档为版本分叉且内容过时/自相矛盾，只保留 `report/design` 一套。 |
| `zynq/pl/rtl/`（旧版） | 已被 `src/pl/rtl` 取代。 |
| `zynq/ps/eeg_ps_aarch64`、`.vvp`、`__pycache__`、Vivado 日志/缓存 | 编译产物与缓存。 |
