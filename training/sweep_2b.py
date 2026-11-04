#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BCI IV 2b 参数扫描：为 baseline_2b.py 找一组合理的默认配置

为什么需要扫描：
  默认的 8-30 Hz + [3,5]s + 朴素 CSP，在 2b 上只做到 kappa≈0.18，
  而文献（Ang 2012 FBCSP）能到 0.60。差距主要来自频带/时间窗/特征，
  先把"朴素 CSP 能到多少"摸清楚，再决定要不要上 FBCSP。

时间窗的硬约束：
  筛查会话 (01T/02T) 想象期 [2, 6] s，反馈会话 (03T/04E/05E) 想象期 [3, 7.5] s。
  两类会话共用的合法区间只有 **[3, 6] s**，所以窗长最多 3 s。
  想用更宽的窗，就必须按会话类型分别设窗（给出 --per-session 思路）。

用法：
  python sweep_2b.py --subjects 1 4 8          # 挑代表性强/中/弱的三个人
  python sweep_2b.py --subjects 1 --protocol cross
"""
import argparse
import itertools
import os
import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from baseline_2b import (SESSIONS_T, SESSIONS_E, collect, score_cv_within,  # noqa: E402
                         score_cross, make_pipeline)

BANDS = [(8, 30), (8, 13), (13, 30), (4, 40), (7, 13), (13, 26), (6, 35)]
WINDOWS = [(3.0, 5.0), (3.0, 6.0), (3.5, 5.5), (4.0, 6.0), (3.0, 4.5), (3.5, 6.0)]


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    raw = os.path.join(here, "..", "data", "raw")
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(raw, "BCICIV_2b_gdf"))
    ap.add_argument("--labels", default=os.path.join(raw, "BCICIV_2b_true_labels"))
    ap.add_argument("--subjects", type=int, nargs="*", default=[1, 4, 8])
    ap.add_argument("--protocol", choices=["within", "cross"], default="within")
    ap.add_argument("--m", type=int, default=2)
    ap.add_argument("--kernel", default="linear")
    ap.add_argument("--C", type=float, default=1.0)
    ap.add_argument("--splits", type=int, default=5)
    ap.add_argument("--repeats", type=int, default=2)
    a = ap.parse_args()

    import mne
    mne.set_log_level("ERROR")

    print("=" * 92)
    print(f"BCI IV 2b 参数扫描 | 协议={a.protocol} | m={a.m} | {a.kernel} C={a.C} | "
          f"{a.splits}×{a.repeats} 折 | 被试 {a.subjects}")
    print("=" * 92)
    hdr = "".join(f"{'B%02d' % s:>8}" for s in a.subjects)
    print(f"{'频带':<12}{'时间窗':<16}{hdr}{'平均acc':>10}{'平均kappa':>11}")
    print("-" * 92)

    best = None
    for band, win in itertools.product(BANDS, WINDOWS):
        cfg = Cfg(tmin=win[0], tmax=win[1], l_freq=band[0], h_freq=band[1],
                  causal=False, keep_artifacts=False)
        accs = []
        try:
            for s in a.subjects:
                if a.protocol == "within":
                    X, y, _, _ = collect(a.data, a.labels, [s], SESSIONS_T, cfg)
                    acc, _ = score_cv_within(X, y, a.m, a.kernel, a.C, a.splits, a.repeats)
                else:
                    Xtr, ytr, _, _ = collect(a.data, a.labels, [s], SESSIONS_T, cfg)
                    Xte, yte, _, _ = collect(a.data, a.labels, [s], SESSIONS_E, cfg)
                    acc, _, _ = score_cross(Xtr, ytr, Xte, yte, a.m, a.kernel, a.C)
                accs.append(acc)
        except Exception as e:
            print(f"{str(band):<12}{str(win):<16}  失败 {type(e).__name__}: {e}")
            continue

        avg = float(np.mean(accs))
        kap = 2 * avg - 1
        cells = "".join(f"{x*100:7.2f}%" for x in accs)
        cells += " " * max(0, 24 - len(cells))
        print(f"{str(band):<12}{str(win):<16}{cells}{avg*100:9.2f}%{kap:11.3f}")
        if best is None or kap > best[0]:
            best = (kap, band, win, avg)

    print("-" * 92)
    if best:
        print(f"最佳：频带 {best[1]}  时间窗 {best[2]}  "
              f"平均 acc {best[3]*100:.2f}%  kappa {best[0]:.3f}")
    print()
    print("对照：Ang et al. 2012 (FBCSP) 在 2b 上平均 kappa 0.600；")
    print("      竞赛官方排名第 2（CSSD+LDA）0.58，第 3（CSP+LDA/SVM）0.46。")


if __name__ == "__main__":
    main()
