#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EOG 回归为什么有害？—— 测试三种实现方式

  ① 逐试次回归（首轮实验用的，结果 -0.92 pp）
  ② 全局回归：整个域的所有试次拼起来估一组系数（标准做法）
  ③ 不用 EOG 回归（对照）

其余流程固定为 FBCSP6 + EA + SVM（首轮最优）。
"""
import os
import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from optimize_2b import (BANDS6, SESS_E, SESS_T, band_filter, butter_sos,  # noqa: E402
                         crop, csp_feat, csp_fit, dc_highpass, euclid_align,
                         load_session, svm_fit_predict)


def eog_regress_global(Xe, Xo):
    """② 全局系数：把所有试次在时间上拼起来，估一组回归系数，再逐试次减"""
    n, C, T = Xe.shape
    A = np.concatenate([Xo, np.ones((n, 1, T))], axis=1).transpose(0, 2, 1)
    Y = Xe.transpose(0, 2, 1)
    A2 = A.reshape(-1, A.shape[2])          # (n*T, 4)
    Y2 = Y.reshape(-1, C)                   # (n*T, C)
    B, *_ = np.linalg.lstsq(A2, Y2, rcond=None)
    fit = (A2 @ B).reshape(Y.shape)
    return (Y - fit).transpose(0, 2, 1)


def eog_regress_pertrial(Xe, Xo):
    """① 逐试次系数（首轮实验用的）"""
    n, C, T = Xe.shape
    A = np.concatenate([Xo, np.ones((n, 1, T))], axis=1).transpose(0, 2, 1)
    Y = Xe.transpose(0, 2, 1)
    AtA = np.einsum("nti,ntj->nij", A, A)
    AtY = np.einsum("nti,ntc->nic", A, Y)
    AtA += np.eye(A.shape[2])[None] * 1e-8
    B = np.linalg.solve(AtA, AtY)
    return (Y - np.einsum("nti,nic->ntc", A, B)).transpose(0, 2, 1)


def eval_fbcsp_ea(Xtr, Xte, ytr, yte, k=8, m=2):
    """FBCSP6 + EA + 线性 SVM"""
    Ftr_p, Fte_p = [], []
    for lo, hi in BANDS6:
        sos = butter_sos(lo, hi)
        a, _ = euclid_align(crop(band_filter(Xtr, sos), 3.5, 6.0))
        b, _ = euclid_align(crop(band_filter(Xte, sos), 3.5, 6.0))
        W = csp_fit(a, ytr, m)
        Ftr_p.append(csp_feat(a, W))
        Fte_p.append(csp_feat(b, W))
    Ftr, Fte = np.hstack(Ftr_p), np.hstack(Fte_p)
    from sklearn.feature_selection import mutual_info_classif
    mi = mutual_info_classif(Ftr, ytr, random_state=0)
    idx = np.argsort(mi)[::-1][:min(k, Ftr.shape[1])]
    Ftr, Fte = Ftr[:, idx], Fte[:, idx]
    mu, sd = Ftr.mean(0), Ftr.std(0)
    sd[sd < 1e-12] = 1
    yh = svm_fit_predict((Ftr - mu) / sd, ytr, (Fte - mu) / sd)
    return float((yh == yte).mean())


def main():
    subs = list(range(1, 10))
    modes = ['无 EOG 回归', '逐试次回归', '全局回归']
    print("=" * 78)
    print("EOG 回归实现方式对比 | FBCSP6 + EA + SVM | 9 被试 cross 协议")
    print("=" * 78)
    print(f"{'被试':<6}" + "".join(f"{m:>14}" for m in modes))
    print('-' * 78)
    res = np.full((len(subs), 3), np.nan)
    for si, s in enumerate(subs):
        try:
            Xt, Ot, yt, kt = [], [], [], []
            for ss in SESS_T:
                e, o, y, kp = load_session(s, ss)
                Xt.append(e); Ot.append(o); yt.append(y); kt.append(kp)
            Xe_, Oe_, ye_, ke_ = [], [], [], []
            for ss in SESS_E:
                e, o, y, kp = load_session(s, ss)
                Xe_.append(e); Oe_.append(o); ye_.append(y); ke_.append(kp)

            Xtr = dc_highpass(np.concatenate(Xt))
            Xte = dc_highpass(np.concatenate(Xe_))
            Otr = dc_highpass(np.concatenate(Ot))
            Ote = dc_highpass(np.concatenate(Oe_))
            ytr = np.concatenate(yt); yte = np.concatenate(ye_)
            ktr = np.concatenate(kt); kte = np.concatenate(ke_)
            Xtr, Otr, ytr = Xtr[ktr], Otr[ktr], ytr[ktr]
            Xte, Ote, yte = Xte[kte], Ote[kte], yte[kte]

            variants = [None, eog_regress_pertrial, eog_regress_global]
            line = f"B{s:02d}   "
            for vi, fn in enumerate(variants):
                Xa = Xtr if fn is None else fn(Xtr, Otr)
                Xb = Xte if fn is None else fn(Xte, Ote)
                acc = eval_fbcsp_ea(Xa, Xb, ytr, yte)
                res[si, vi] = acc
                line += f"{acc*100:13.2f}%"
            print(line, flush=True)
        except Exception as ex:
            print(f"B{s:02d}  失败: {type(ex).__name__}: {ex}")

    print('-' * 78)
    m = np.nanmean(res, 0)
    print(f"{'平均':<6}" + "".join(f"{v*100:13.2f}%" for v in m))
    print(f"{'kappa':<6}" + "".join(f"{2*v-1:14.3f}" for v in m))
    print()
    print("判读：")
    print(f"  逐试次回归相对无回归: {(m[1]-m[0])*100:+.2f} pp")
    print(f"  全局回归  相对无回归: {(m[2]-m[0])*100:+.2f} pp")
    np.save(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "params", "eog_variants.npy"), res)


if __name__ == '__main__':
    main()
