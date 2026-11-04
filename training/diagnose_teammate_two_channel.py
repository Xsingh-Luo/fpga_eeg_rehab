"""Describe cross-recording shift and noise; never select a model on test labels."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'validation/two_channel_20261102'
sys.path.insert(0, str(OUT/'pydeps'))
import json
import numpy as np
from scipy import signal
from train_teammate_two_channel import load_session, FBCSP


def main():
    model = json.loads((OUT/'model_candidate.json').read_text())
    report = json.loads((OUT/'training_report.json').read_text())
    transformer = FBCSP()
    transformer.weights_ = np.asarray(model['csp_weights_bands_channels_components'])
    means = np.asarray(model['standardization_train_mean'])
    scales = np.asarray(model['standardization_train_scale'])
    cases = [('training_60','20261031','subj01_s1_mi60'),
             ('holdout_old_20','20261031','subj01_test20'),
             ('holdout_new_20','20261102','subj01_test20')]
    stats = []
    for name, folder, stem in cases:
        X, y, trials, hashes, counts, filtered, meta = load_session(folder, stem, np.asarray(model['causal_sos']))
        f = transformer.transform(X)
        standard = (f-means)/scales
        raw_epochs = np.stack([counts[t['window_start']:t['window_stop_exclusive']].astype(float) for t in trials])
        # Frequency QC is computed on the same evaluated windows, in ADC count units.
        freq, psd = signal.welch(raw_epochs, fs=250, nperseg=500, noverlap=250, axis=1, detrend='linear')
        def power(lo, hi):
            return np.trapezoid(psd[:, (freq>=lo)&(freq<=hi), :], x=freq[(freq>=lo)&(freq<=hi)], axis=1)
        p_mi = power(8,32)
        p50, p60 = power(48,52), power(58,62)
        row = {'session':name, 'trials':len(y),
               'median_band_rms_count_bands_by_C3_C4':np.median(np.std(X,axis=2),axis=0).tolist(),
               'median_feature_shift_in_training_sd':np.median(standard,axis=0).tolist(),
               'fraction_features_outside_train_3sd':float(np.mean(np.abs(standard)>3)),
               'median_50Hz_to_8_32_power_ratio_C3_C4':np.median(p50/np.maximum(p_mi,1e-30),axis=0).tolist(),
               'median_60Hz_to_8_32_power_ratio_C3_C4':np.median(p60/np.maximum(p_mi,1e-30),axis=0).tolist(),
               'label_window_duration_s':626/250,
               'median_decision_by_true_class':{str(c):float(np.median((f@np.asarray(model['svm_folded_w'])+model['svm_folded_b'])[y==c])) for c in (0,1)}}
        stats.append(row)
    (OUT/'signal_diagnostics.json').write_text(json.dumps(stats,indent=2),encoding='utf-8')
    gate = {'deploy':False, 'reason':'Both untouched 20-trial tests are 50%, predicting only right; blocked training CV 51.67%. No reliable classification established.',
            'model_is_candidate_only':True, 'board_parameters_replaced':False,
            'next_step':'Verify trial behavior and C3/C4 signal quality; collect fresh labeled calibration and reserved test runs. Existing 20-trial test files are now evaluated and must not be used as fresh validation for further tuning.'}
    (OUT/'deployment_gate.json').write_text(json.dumps(gate,indent=2),encoding='utf-8')
    model['deployment_status']='BLOCKED_BY_OFFLINE_ACCURACY; DO_NOT_DEPLOY'
    (OUT/'model_candidate.json').write_text(json.dumps(model,indent=2),encoding='utf-8')
    text = '''# C3/C4 两通道离线基线结果

实际接线采用用户确认的 C3=CH3、C4=CH4，排除饱和的 Cz=CH7；忽略原文件中错误的通道名称。

## 结果

| 评估 | 结果 | 含义 |
|---|---|---|
| 60次训练数据内部5折（用于选参数） | 56.67% | 用于选参数，不能当最终准确率 |
| 60次训练数据连续试次分组5折 | 51.67% | 接近均衡二分类机会水平 |
| 独立20次：目录20261031 | 10/20，50% | 全部预测右手 |
| 独立20次：目录20261102 | 10/20，50% | 全部预测右手 |

每个20次测试的Wilson 95%区间约29.9%～70.1%，样本少，不支持稳定识别结论。两次均预测同一类别，应判为当前模型不可用，而不是正常左右手控制。

## 方法和边界

250Hz，连续因果去直流及8–16、16–24、24–32Hz滤波（系数沿用PL）；每个提示后1秒开始取626点，共2.504秒。每个试次只出一个评估预测，未将重叠窗口当作独立试次。CSP、标准化和线性SVM都在训练折内拟合；仅用60次记录选择参数，两组20次文件不参与选择。所有100次试次均保留，未按测试表现删试次。

信号处理采用浮点状态、已量化滤波系数和Q14投影，尚非完整逐位RTL模型。虽然已生成三输入结构的CSP参数候选（中间Cz权重为零），因为分类未达可用水平，没有导出板端安装包，也没有替换开发板参数。

两个测试文件已被评估。之后任何基于这些结果的改进都需要新的预留测试，不能反复调参后仍称原测试完全独立。数据只有一位被试，不能推广为跨人准确率；事件使用主机采样编号，无硬件标记同步。直流偏置大或Cz饱和本身不能证明C3/C4没有运动想象信息；当前基线结果也不能证明两通道方案原理不可行。

## 交付文件

- training_report.json：协议、参数搜索、测试混淆矩阵、输入哈希。
- trial_predictions.csv：逐试次标签、窗口索引、决策与预测。
- model_candidate.json：仅供离线复现，禁止部署的模型候选。
- deployment_gate.json：不部署的原因。
- signal_diagnostics.json：频带幅值、工频能量和跨记录特征漂移。
- holdout_confusion.png：两个独立测试的混淆矩阵。
- epochs_and_labels.npz：预处理后的100个试次。

依赖只安装在本目录pydeps/，未更改系统Python。复现命令：

```powershell
& D:/Environment/miniconda3/python.exe FPGA2026QS/python/train_teammate_two_channel.py
& D:/Environment/miniconda3/python.exe FPGA2026QS/python/diagnose_teammate_two_channel.py
```
'''
    (OUT/'RESULTS.md').write_text(text,encoding='utf-8')
    notch_dir = OUT/'notch50_exploratory'
    if (notch_dir/'training_report.json').exists():
        notch = json.loads((notch_dir/'training_report.json').read_text())
        candidate_path = notch_dir/'model_candidate.json'
        candidate = json.loads(candidate_path.read_text())
        candidate['deployment_status'] = 'BLOCKED_BY_OFFLINE_ACCURACY_AND_NO_FRESH_VALIDATION; DO_NOT_DEPLOY'
        candidate_path.write_text(json.dumps(candidate,indent=2),encoding='utf-8')
        (notch_dir/'deployment_gate.json').write_text(json.dumps({
            'deploy':False, 'reason':'Exploratory notch control gives 50%/60% on previously evaluated tests, blocked training CV 46.67%; insufficient evidence for stable recognition.',
            'fresh_test_required':True, 'additional_PS_notch_would_be_required':True},indent=2),encoding='utf-8')
        text += '''
## 工频及探索性对照

各想象评估窗中，用Welch功率谱计算48–52Hz/8–32Hz能量比，最新记录C3/C4的试次中位数分别约191/177；训练记录约55/32。此比值与观察到的跨记录特征漂移支持优先检查接触、参考和环境干扰，但不能单独证明分类失败的全部原因。

随后增加因果50Hz、Q=30陷波对照。由于原测试标签和结果已经看过，该对照属于探索性复用测试，不能再称为全新的独立验证：训练随机5折61.67%，连续试次分组5折46.67%；旧20次50%，新20次60%（12/20）。结果仍不支持可靠识别，未部署。陷波步骤若未来采用，还需要PS端实现并验证，不能声称只换参数即可保持原始数据通路等价。

建议先核对C3/CH3、C4/CH4在历次采集中的实际安装位置、提示任务是否确为第一人称运动想象及参考接触；改善信号后，同一佩戴条件重新采集带标签训练记录和独立预留测试。已有测试可用于诊断，不能反复试参数追求最高准确率后作为最终验收。
'''
        (OUT/'RESULTS.md').write_text(text,encoding='utf-8')
    import importlib.metadata
    versions = {name:importlib.metadata.version(name) for name in ('numpy','scipy','scikit-learn','matplotlib')}
    (OUT/'dependency_versions.json').write_text(json.dumps(versions,indent=2),encoding='utf-8')
    print(json.dumps({'deployment_gate':gate,'signal_diagnostics':stats},indent=2))


if __name__ == '__main__':
    main()
