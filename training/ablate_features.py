#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
消融实验：为什么本次可视化 B01 得 60.3%，而 baseline_2b.py 得 68.1%？

两个可疑变量：
  A. 特征定义：归一化 log(var/Σvar)  vs  MNE 的 log(var)
  B. 滤波相位：因果(在线)  vs  零相位(离线)
固定 CSP 训练实现（自己写的），只改这两个变量，跑 2×2 = 4 组。

用法: python ablate_features.py [--subjects 1 2 3 ...]
"""
import argparse
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
from viz_classification import (DATA, cheby2_sos, csp_train, fit_linear_svm,  # noqa: E402
                                preprocess)
from scipy.io import loadmat  # noqa: E402


def feats(X, W, normalize):
    Z = np.einsum("cj,nct->njt", W, X)          # z = W^T x（W 的列是滤波器）
    v = np.maximum(Z.var(axis=2), 1e-30)
    return np.log(v / v.sum(axis=1, keepdims=True)) if normalize else np.log(v)


def run(subj, sos):
    f = os.path.join(DATA, f"subj{subj:02d}.mat")
    D = loadmat(f, squeeze_me=True, struct_as_record=False)
    meta = D["meta"]
    if hasattr(meta, "_fieldnames"):
        meta = {k: getattr(meta, k) for k in meta._fieldnames}
    meta["win_export"] = [float(v) for v in np.atleast_1d(meta["win_export"]).ravel()]
    ytr = np.asarray(D["ytr"]).ravel().astype(int)
    yte = np.asarray(D["yte"]).ravel().astype(int)

    out = {}
    for causal in (True, False):
        Xtr = preprocess(D["Xtr"], meta, sos, causal)
        Xte = preprocess(D["Xte"], meta, sos, causal)
        W, _ = csp_train(Xtr, ytr, m=2)
        for norm in (True, False):
            Ftr, Fte = feats(Xtr, W, norm), feats(Xte, W, norm)
            w, b, _ = fit_linear_svm(Ftr, ytr)
            acc = float((((Fte @ w + b) > 0).astype(int) == yte).mean())
            out[(causal, norm)] = acc
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subjects", type=int, nargs="*", default=list(range(1, 10)))
    a = ap.parse_args()
    sos = cheby2_sos()

    print("=" * 84)
    print("消融：特征定义 × 滤波相位    （cross 协议，B01..B09）")
    print("=" * 84)
    print(f"{'被试':<6}{'因果+归一化':>14}{'因果+log(var)':>16}"
          f"{'零相位+归一化':>16}{'零相位+log(var)':>18}")
    print("-" * 84)

    agg = {}
    for s in a.subjects:
        try:
            r = run(s, sos)
        except Exception as e:
            print(f"B{s:02d}   失败: {e}"); continue
        for k, v in r.items():
            agg.setdefault(k, []).append(v)
        print(f"B{s:02d}   {r[(True,True)]*100:13.2f}%{r[(True,False)]*100:15.2f}%"
              f"{r[(False,True)]*100:15.2f}%{r[(False,False)]*100:17.2f}%")

    print("-" * 84)
    keys = [(True, True), (True, False), (False, True), (False, False)]
    means = {k: float(np.mean(agg[k])) * 100 for k in keys if k in agg}
    print(f"{'平均':<6}{means.get((True,True),0):13.2f}%{means.get((True,False),0):15.2f}%"
          f"{means.get((False,True),0):15.2f}%{means.get((False,False),0):17.2f}%")
    print()
    print("对照基线：baseline_2b.py（MNE CSP log=True = log(var) + 零相位）"
          " B01 68.12%，9 人平均 74.37%")
    print()
    print("解读：")
    dn = means.get((True,True),0) - means.get((True,False),0)
    dc = means.get((True,False),0) - means.get((False,False),0)
    print(f"  · 归一化的影响（因果下）      : {dn:+.2f} 个百分点")
    print(f"  · 因果滤波的影响（log(var) 下）: {dc:+.2f} 个百分点")


if __name__ == "__main__":
    main()
