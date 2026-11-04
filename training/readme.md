# Historical training and current inference reference

本目录保留实际使用的训练/分析程序；整理时只迁移参考模块和ROM路径，未改数学实现。训练的输入需要另行按data/sources.md准备，个人原始脑电不在公开包内。原程序中的validation输出/数据路径是历史运行布局，需要对应的私有录制及参数文件；不能声称全部脚本无需原数据即可运行。

可直接复核的冻结结果：python sim/verify_integer_replay.py，使用本包的20试次、参数和原整数参考；不重新训练，不访问硬件。python sim/check_release.py检查包、输入和ABI。重新训练与新受试者验收是不同任务。
