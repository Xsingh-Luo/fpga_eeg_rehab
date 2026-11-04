# Data sources

BCI Competition IV data set2b，Graz data set B：https://www.bbci.de/competition/iv/；说明：https://www.bbci.de/competition/iv/desc_2b.pdf 。原始数据3路双极EEG（C3/Cz/C4）、250Hz、左右手两类，另含不用于本分类的EOG。

本项目使用B04：01T/02T/03T训练420试次，04E/05E评价320试次；本地subj04.mat SHA256=802635475063e51aa8e3ed651411d751dcf8a7bbfe33ad4ba897bc36a19ab52f。MATLAB数据与官方标签逐项核对，原始训练/评价试次无完全相同重叠。回放是评价文件顺序前20次，不按成功与否挑选；转换为33字节Cyton格式，标签0=左、1=右。

原始GDF、MAT文件保存在私有工程数据目录，不在本公开候选包重复分发整个数据集。按官方入口获取后保留其引用及使用条件。training目录保存原始训练/分析程序；其中私有数据路径和历史布局需要按照文件中的输入说明准备，不宣称无需数据即可重新训练。

个人自采数据为三次修正电极后的录制，共60试次，实时只用C3/C4；不默认公开受试者原始脑电。模型参数、方法及聚合指标用于复现推理，新的实时效果需独立采集评价。
