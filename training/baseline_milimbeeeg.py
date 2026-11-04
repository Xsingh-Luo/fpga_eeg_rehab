#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MILimbEEG 数据集 —— OpenBCI Cyton+Daisy 实采 EEG 的运动想象二分类
（Asanza et al., Data in Brief 50 (2023) 109540）

════════════════════════════════════════════════════════════════════
数据事实（从仓库 README 与 fLabelEEG.m 解出，非猜测）
────────────────────────────────────────────────────────────────────
  硬件      : OpenBCI Cyton + Daisy，16 通道，125 Hz，干电极，单极
  单文件    : 501 行 × 17 列（第 1 列采样序号 + 16 通道 EEG，µV）
              500 点 @125 Hz = 4 s
  文件命名  : S{被试}R{轮次}{I|M}{任务}_{序号}.csv
              I = 运动想象(Motor Imagery)，M = 实际运动(Motor Execution)
  标签      : 1=BEO 2=LCH左手 3=RCH右手 4=LDF 5=LPF 6=RDF 7=RPF 8=Rest
              （M 系列 +8，即 9..16）

电极 → 列号（README 的连线表，**注意 CH5 在原文重复写成 FC5，疑似笔误**）
  0=FC5  1=F3   2=FZ   3=F4   4=FC5?  5=FC1  6=FC2  7=CZ
  8=T3   9=CP5 10=C3  11=CP1 12=CP2  13=C4  14=CP6 15=T4

════════════════════════════════════════════════════════════════════
⚠️ 这个数据集的正确用法（别踩坑）
────────────────────────────────────────────────────────────────────
  每个被试每个任务只有 **5 个试次**（I2_1..I2_5）。
  5 个试次估不出可靠的 16×16 协方差矩阵 —— **单被试 CSP 在这里不成立**。
  所以本脚本默认做 **跨被试池化 + 按被试分组的交叉验证**，
  这才是该数据集设计的用途。

  它真正的价值在于：这是**真实 OpenBCI 硬件采集**的数据，
  可用于验证 PS 中转 / PL 流水线的数据通路、以及决赛回放演示。

用法
────────────────────────────────────────────────────────────────────
  python baseline_milimbeeeg.py --task imagery --channels motor3
  python baseline_milimbeeeg.py --task motor   --channels all
  python baseline_milimbeeeg.py --subjects 1 2 3 --channels c3czc4
"""
import argparse
import os
import re
import sys
from glob import glob

import numpy as np
import warnings

warnings.filterwarnings("ignore")

# Windows 控制台默认 GBK，中文会乱码、emoji 会直接抛异常
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 电极名 → CSV 列号
ELEC_TO_COL = {
    "FC5": 0, "F3": 1, "FZ": 2, "F4": 3, "FC6": 4, "FC1": 5, "FC2": 6, "CZ": 7,
    "T3": 8, "CP5": 9, "C3": 10, "CP1": 11, "CP2": 12, "C4": 13, "CP6": 14, "T4": 15,
}

PRESETS = {
    # 与 BCI Competition IV 2b 完全对齐的三通道配置
    "c3czc4": ["C3", "CZ", "C4"],
    # 单侧运动皮层覆盖（含 C3/C4 周边），Cyton 8 通道的典型 MI 蒙太奇
    "motor8": ["FC1", "FC2", "CZ", "C3", "C4", "CP1", "CP2", "CP5"],
    # 加宽的运动相关区
    "motor3": ["C3", "CZ", "C4"],
    "all": list(ELEC_TO_COL.keys()),
}

# 任务号：2=左手，3=右手
TASK_LEFT, TASK_RIGHT = 2, 3


def find_files(data_dir, subjects, task_flag, task_no):
    """task_flag: 'I' 想象 | 'M' 运动"""
    out = []
    for s in subjects:
        d = os.path.join(data_dir, f"S{s}")
        if not os.path.isdir(d):
            continue
        pat = re.compile(rf"^S{s}R(\d+){task_flag}{task_no}_(\d+)\.csv$")
        for f in sorted(os.listdir(d)):
            m = pat.match(f)
            if m:
                out.append((s, int(m.group(1)), int(m.group(2)), os.path.join(d, f)))
    return out


def load_csv(path):
    """返回 (16, 500) float32，单位 µV"""
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        rows = fh.read().strip().split("\n")
    body = rows[1:]                                   # 去掉表头
    arr = np.array([[float(v) for v in r.split(",")[1:17]] for r in body], dtype=np.float32)
    return arr.T                                      # → (16 通道, 500 时间点)


def build_dataset(data_dir, subjects, task, channels, crop):
    """返回 X (n_trials, n_ch, n_times), y, groups"""
    flag = "I" if task == "imagery" else "M"
    cols = [ELEC_TO_COL[c] for c in channels]

    X, y, g = [], [], []
    for label, tno in ((0, TASK_LEFT), (1, TASK_RIGHT)):
        for (s, r, k, path) in find_files(data_dir, subjects, flag, tno):
            try:
                d = load_csv(path)[cols, :]
            except Exception as e:
                print(f"  [warn] {os.path.basename(path)}: {e}")
                continue
            if crop:
                a, b = crop
                d = d[:, a:b]
            X.append(d)
            y.append(label)
            g.append(s)
    if not X:
        raise RuntimeError("没有读到任何文件，检查 --data / --subjects 是否正确")
    return np.stack(X), np.array(y), np.array(g)


def bandpass(X, fs, lo, hi):
    """零相位 Butterworth 带通（离线分析用；在线要换因果滤波）"""
    from scipy.signal import butter, filtfilt

    b, a = butter(4, [lo / (fs / 2), hi / (fs / 2)], btype="band")
    return filtfilt(b, a, X, axis=-1).astype(np.float32)


def evaluate(X, y, groups, m, kernel, C, n_splits):
    from mne.decoding import CSP
    from sklearn.model_selection import GroupKFold, cross_val_score
    from sklearn.pipeline import Pipeline
    from sklearn.svm import SVC

    # CSP 前按 trial 去均值（CSP 假定零均值）
    X = X - X.mean(axis=2, keepdims=True)

    n_groups = len(np.unique(groups))
    n_splits = min(n_splits, n_groups)
    cv = GroupKFold(n_splits=n_splits)
    pipe = Pipeline([
        ("csp", CSP(n_components=2 * m, reg="ledoit_wolf", log=True, norm_trace=False)),
        ("svm", SVC(kernel=kernel, C=C)),
    ])
    scores = cross_val_score(pipe, X, y, cv=cv, groups=groups, scoring="accuracy")
    return scores


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="MILimbEEG (OpenBCI) 运动想象二分类")
    ap.add_argument("--data", default=os.path.join(
        here, "..", "third_party", "openbci", "BCI_Motor_Imagery_Task_OpenBCI", "MatlabCode", "data"))
    ap.add_argument("--subjects", type=int, nargs="*", default=list(range(1, 25)))
    ap.add_argument("--task", choices=["imagery", "motor"], default="imagery",
                    help="imagery=运动想象(I) / motor=实际运动(M)")
    ap.add_argument("--channels", choices=list(PRESETS), default="c3czc4")
    ap.add_argument("--fs", type=float, default=125.0)
    ap.add_argument("--l-freq", type=float, default=8.0)
    ap.add_argument("--h-freq", type=float, default=30.0)
    ap.add_argument("--crop", type=int, nargs=2, default=None,
                    help="裁剪时间窗（采样点），如 --crop 25 275 表示去掉前 0.2 s")
    ap.add_argument("--m", type=int, default=2)
    ap.add_argument("--kernel", choices=["linear", "rbf"], default="linear")
    ap.add_argument("--C", type=float, default=1.0)
    ap.add_argument("--splits", type=int, default=8, help="GroupKFold 折数（按被试分组）")
    a = ap.parse_args()

    import mne
    mne.set_log_level("ERROR")

    chans = PRESETS[a.channels]
    print("=" * 78)
    print("MILimbEEG · OpenBCI Cyton+Daisy 16ch @125Hz")
    print(f"任务={a.task} | 通道={a.channels}{chans} | 带通={a.l_freq}-{a.h_freq}Hz "
          f"| 核={a.kernel} | CSP m={a.m}")
    print("=" * 78)

    X, y, g = build_dataset(a.data, a.subjects, a.task, chans, a.crop)
    X = bandpass(X, a.fs, a.l_freq, a.h_freq)

    print(f"样本: {X.shape[0]} 试次 × {X.shape[1]} 通道 × {X.shape[2]} 点 "
          f"({X.shape[2]/a.fs:.2f} s)")
    print(f"类别: 左手 {int((y==0).sum())} / 右手 {int((y==1).sum())}")
    print(f"被试: {len(np.unique(g))} 人（每人类别数 "
          f"{int((y==0).sum())/len(np.unique(g)):.0f} vs {int((y==1).sum())/len(np.unique(g)):.0f}）")
    print("-" * 78)

    scores = evaluate(X, y, g, a.m, a.kernel, a.C, a.splits)
    print(f"按被试分组 {len(scores)} 折交叉验证准确率（跨被试泛化）：")
    for i, s in enumerate(scores, 1):
        print(f"  折 {i}: {s*100:6.2f}%")
    print("-" * 78)
    print(f"平均 {scores.mean()*100:.2f}%  ±  {scores.std()*100:.2f}%   "
          f"(随机水平 50%)")
    print()
    print("[注意] 这是【跨被试】结果，天然低于单被试内部交叉验证。")
    print("       单被试 CSP 在本数据集上不成立：每类只有 5 个试次。")
    print("       做 CSP+SVM 的精度基线请用 BCI IV 2b（baseline_2b.py）。")


if __name__ == "__main__":
    main()
