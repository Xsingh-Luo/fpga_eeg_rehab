#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BCI IV 2a 通道数消融：3 导联 vs 8 导联（同一批试次、同一分类器）

目的
────
回答一个具体问题：把 OpenBCI Cyton 的 8 导联接上以后，比现在 2b 的
3 导联（C3/Cz/C4）到底能多拿多少准确率？本脚本只做“通道数”这一个变量，
不引入 FBCSP / EA，避免把“多几维特征”和“多几个通道”混在一起。

数据事实（已用 MNE 核对）
────────────────────────
  BCI IV 2a：9 被试 × 2 session，22 EEG + 3 EOG，250 Hz，4 类。
  本脚本只取 T session 的 769（左手）/ 770（右手），每人 144 试次。
  2a 的 22 通道标准顺序（MNE 会把中间 17 个名字匿名成 EEG-0..16）：
    0 Fz    1 FC3   2 FC1   3 FCz   4 FC2   5 FC4
    6 C5    7 C3    8 C1    9 Cz   10 C2   11 C4
   12 C6   13 CP3  14 CP1  15 CPz  16 CP2  17 CP4
   18 P1   19 Pz   20 P2   21 POz
  8 导联取的就是本项目计划的自采蒙太奇：
    C3, Cz, C4, FC3, FC4, CP3, CP4, Pz

评估口径
────────
  2a T session 内 5 折 × 3 次重复交叉验证，CSP + 线性 SVM，8–30 Hz。
  绝对值不能和 2b 的 80.32% 直接比（数据集、试次数、协议都不同）；
  只有“3ch → 8ch 的差值”有意义。

用法
────
  python ablate_channels_2a.py
  python ablate_channels_2a.py --subjects 1 3 8 --m 2
  python ablate_channels_2a.py --out channel_ablation_2a.json
"""
import argparse
import json
import os
import sys
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 2a 标准 22 通道顺序（MNE 的匿名通道按此顺序对应）
CH22 = ["Fz", "FC3", "FC1", "FCz", "FC2", "FC4",
        "C5", "C3", "C1", "Cz", "C2", "C4",
        "C6", "CP3", "CP1", "CPz", "CP2", "CP4",
        "P1", "Pz", "P2", "POz"]

MONTAGES = {
    "3ch": ["C3", "Cz", "C4"],
    "8ch": ["C3", "Cz", "C4", "FC3", "FC4", "CP3", "CP4", "Pz"],
}


def resolve_indices(ch_names):
    """把标准 2a 通道名映射到 MNE 实际列号。"""
    idx = {}
    for name in CH22:
        # 只认精确名字。★ 不能拿 EEG-<pos> 兜底：EEG-1 是 FC1 的匿名名，
        # 不是 FC3；早期版本在这里把 8ch 蒙太奇整体错位，准确率会差 5 pp。
        cand = [f"EEG-{name}", f"EEG:{name}", name]
        for c in cand:
            if c in ch_names:
                idx[name] = ch_names.index(c)
                break
    # 对匿名通道，MNE 的 EEG-k 是按 22 通道顺序中未命名通道依次编号的：
    # 0=FC3, 1=FC1, 2=FCz, 3=FC2, 4=FC4, 5=C5, 6=C1, 7=C2, 8=C6,
    # 9=CP3, 10=CP1, 11=CPz, 12=CP2, 13=CP4, 14=P1, 15=P2, 16=POz
    anon = [n for n in CH22 if n not in idx]
    for k, name in enumerate(anon):
        c = f"EEG-{k}"
        if c in ch_names:
            idx[name] = ch_names.index(c)
    missing = [n for n in CH22 if n not in idx]
    if missing:
        raise RuntimeError(f"2a 通道映射失败，缺 {missing}；实际通道={ch_names}")
    return idx


def load_subject(path, tmin, tmax, l_freq, h_freq):
    import mne
    mne.set_log_level("ERROR")
    raw = mne.io.read_raw_gdf(path, preload=True, verbose="ERROR")
    raw.filter(l_freq, h_freq, fir_design="firwin", verbose="ERROR")
    ev, em = mne.events_from_annotations(raw, verbose="ERROR")
    cues = ev[np.isin(ev[:, 2], [em["769"], em["770"]])]
    y = np.where(cues[:, 2] == em["769"], 0, 1)
    X = mne.Epochs(raw, cues, tmin=tmin, tmax=tmax, baseline=None,
                   preload=True, verbose="ERROR").get_data()
    X = X - X.mean(axis=2, keepdims=True)   # 逐试次去均值，和在线高通等价
    return X, y, raw.ch_names


def score(X, y, sel, m, splits, repeats, seed=42):
    from mne.decoding import CSP
    from sklearn.model_selection import RepeatedStratifiedKFold, cross_val_score
    from sklearn.pipeline import Pipeline
    from sklearn.svm import SVC

    n_comp = min(2 * m, len(sel))
    pipe = Pipeline([
        ("csp", CSP(n_components=n_comp, reg="ledoit_wolf", log=True, norm_trace=False)),
        ("svm", SVC(kernel="linear", C=1.0)),
    ])
    cv = RepeatedStratifiedKFold(n_splits=splits, n_repeats=repeats, random_state=seed)
    sc = cross_val_score(pipe, X[:, sel, :], y, cv=cv, scoring="accuracy")
    return float(sc.mean()), float(sc.std()), n_comp


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="BCI IV 2a 3ch vs 8ch 通道消融")
    ap.add_argument("--data", default=os.path.join(here, "..", "data", "raw", "BCICIV_2a_gdf"))
    ap.add_argument("--subjects", type=int, nargs="*", default=list(range(1, 10)))
    ap.add_argument("--tmin", type=float, default=1.0, help="相对 cue 的起点（秒）")
    ap.add_argument("--tmax", type=float, default=4.0, help="相对 cue 的终点（秒）")
    ap.add_argument("--l-freq", type=float, default=8.0)
    ap.add_argument("--h-freq", type=float, default=30.0)
    ap.add_argument("--m", type=int, default=1, help="每侧 CSP 对数；n_comp=min(2m,N)")
    ap.add_argument("--splits", type=int, default=5)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--include-22ch", action="store_true", help="加上 22 导联上界")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    monts = dict(MONTAGES)
    if a.include_22ch:
        monts["22ch"] = CH22

    print("=" * 78)
    print(f"BCI IV 2a | 3ch vs 8ch | 窗=[{a.tmin},{a.tmax}]s(相对 cue) | "
          f"带通={a.l_freq}-{a.h_freq}Hz | m={a.m}")
    print(f"评估：{a.splits} 折 × {a.repeats} 次，CSP + 线性 SVM")
    print("=" * 78)

    rows = {k: [] for k in monts}
    t0 = time.time()
    for s in a.subjects:
        p = os.path.join(a.data, f"A{s:02d}T.gdf")
        if not os.path.exists(p):
            print(f"A{s:02d}  跳过：{p} 不存在")
            continue
        X, y, ch_names = load_subject(p, a.tmin, a.tmax, a.l_freq, a.h_freq)
        idx = resolve_indices(ch_names)
        line = [f"A{s:02d} n={len(y):3d}"]
        for name, names in monts.items():
            sel = [idx[n] for n in names]
            acc, sd, nc = score(X, y, sel, a.m, a.splits, a.repeats)
            rows[name].append(acc)
            line.append(f"{name}={acc*100:5.2f}(±{sd*100:4.1f},c={nc})")
        print("  ".join(line), flush=True)

    print("-" * 78)
    summary = {}
    for name, vals in rows.items():
        if not vals:
            continue
        summary[name] = float(np.mean(vals))
        print(f"{name:5s} 平均 {np.mean(vals)*100:6.2f}%  ± {np.std(vals)*100:5.2f}  (n={len(vals)})")
    if "3ch" in summary and "8ch" in summary:
        print(f"8ch - 3ch = {(summary['8ch']-summary['3ch'])*100:+.2f} pp")
    if "8ch" in summary and "22ch" in summary:
        print(f"22ch - 8ch = {(summary['22ch']-summary['8ch'])*100:+.2f} pp")
    print(f"耗时 {time.time()-t0:.1f} s")

    if a.out:
        try:
            with open(a.out, "w", encoding="utf-8") as f:
                json.dump({"config": vars(a), "mean": summary,
                           "per_subject": {k: v for k, v in rows.items()}},
                          f, indent=2, ensure_ascii=False)
            print(f"已写入 {os.path.abspath(a.out)}")
        except OSError as e:
            # 某些沙箱只允许写工作区根目录；这里不让“写盘失败”吞掉已算出的结果
            print(f"结果写盘失败（{e}）；以上打印结果仍然有效")


if __name__ == "__main__":
    main()
