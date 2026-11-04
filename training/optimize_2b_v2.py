#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EA 落地性验证 + 进一步优化探索

  实验 1 ★ 校准数据量敏感性 —— EA 需要多少校准数据？（决定能否现场演示）
  实验 2    EA 变体：算术均值 vs 黎曼均值
  实验 3    超参数：特征选择 k、CSP m
  实验 4    子带数（6 带 vs 9 带 vs 4 带）

基线管线：FBCSP + EA + EOG(全局回归) + 线性 SVM，cross 协议，9 被试。
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
                         crop, csp_feat, csp_fit, dc_highpass, load_session,
                         svm_fit_predict)
from test_eog_variants import eog_regress_global  # noqa: E402

BANDS9 = [(4, 8), (8, 12), (12, 16), (16, 20), (20, 24),
          (24, 28), (28, 32), (32, 36), (36, 40)]
BANDS4 = [(8, 14), (14, 20), (20, 26), (26, 32)]


# ═══════════════════════════════════════════════════════════
def cov_trials(X):
    """逐试次协方差（迹归一化）"""
    n = X.shape[0]
    R = np.zeros((X.shape[1], X.shape[1], n))
    for i in range(n):
        S = X[i] @ X[i].T
        R[:, :, i] = S / max(np.trace(S), 1e-30)
    return R


def mean_arith(R):
    return R.mean(2)


def mean_riemann(R, n_iter=8, tol=1e-6):
    """黎曼均值（Barachant 迭代法）"""
    M = R.mean(2)
    for _ in range(n_iter):
        d, U = np.linalg.eigh((M + M.T) / 2)
        d = np.maximum(d, 1e-12)
        Mh = U @ np.diag(d ** 0.5) @ U.T
        Mhi = U @ np.diag(d ** -0.5) @ U.T
        S = np.einsum("ij,jkn,kl->iln", Mhi, R, Mhi).mean(2)
        Mnew = Mh @ S @ Mh
        if np.linalg.norm(Mnew - M) / max(np.linalg.norm(M), 1e-12) < tol:
            M = Mnew
            break
        M = Mnew
    return (M + M.T) / 2


def whiten_mat(Rbar):
    d, U = np.linalg.eigh((Rbar + Rbar.T) / 2)
    d = np.maximum(d, 1e-12)
    return U @ np.diag(d ** -0.5) @ U.T


def align_with(X, M):
    return np.einsum("ij,njt->nit", M, X)


def align_self(X, mode="arith", n_cal=None):
    """用 X 的前 n_cal 个试次估计 M 并白化（n_cal=None 用全部）"""
    Xc = X if n_cal is None else X[:n_cal]
    R = cov_trials(Xc)
    Rbar = mean_riemann(R) if mode == "riemann" else mean_arith(R)
    return align_with(X, whiten_mat(Rbar))


# ═══════════════════════════════════════════════════════════
def pipeline(bands, Xtr, Xte, ytr, yte, m=2, k=8, ea_mode="arith",
             n_cal=None, do_ea=True, Mtr=None, Mte=None):
    """返回准确率；Mtr/Mte 若给定则复用（用于只变 n_cal 的场景）"""
    Ftr_p, Fte_p = [], []
    for lo, hi in bands:
        sos = butter_sos(lo, hi)
        a = crop(band_filter(Xtr, sos), 3.5, 6.0)
        b = crop(band_filter(Xte, sos), 3.5, 6.0)
        if do_ea:
            Ra = mean_riemann(cov_trials(a)) if ea_mode == "riemann" else mean_arith(cov_trials(a))
            Ma = Mtr if Mtr is not None else whiten_mat(Ra)
            if Mte is not None:
                Mb = Mte
            else:
                Xc = b if n_cal is None else b[:n_cal]
                Rb = (mean_riemann(cov_trials(Xc)) if ea_mode == "riemann"
                      else mean_arith(cov_trials(Xc)))
                Mb = whiten_mat(Rb)
            a = align_with(a, Ma)
            b = align_with(b, Mb)
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
    return float((svm_fit_predict((Ftr - mu) / sd, ytr, (Fte - mu) / sd) == yte).mean())


def load_subject(s):
    Xt, Ot, yt, kt = [], [], [], []
    for ss in SESS_T:
        e, o, y, kp = load_session(s, ss)
        Xt.append(e); Ot.append(o); yt.append(y); kt.append(kp)
    Xe_, Oe_, ye_, ke_ = [], [], [], []
    for ss in SESS_E:
        e, o, y, kp = load_session(s, ss)
        Xe_.append(e); Oe_.append(o); ye_.append(y); ke_.append(kp)
    Xtr = dc_highpass(np.concatenate(Xt)); Xte = dc_highpass(np.concatenate(Xe_))
    Otr = dc_highpass(np.concatenate(Ot)); Ote = dc_highpass(np.concatenate(Oe_))
    ytr = np.concatenate(yt); yte = np.concatenate(ye_)
    ktr = np.concatenate(kt); kte = np.concatenate(ke_)
    Xtr, Otr, ytr = Xtr[ktr], Otr[ktr], ytr[ktr]
    Xte, Ote, yte = Xte[kte], Ote[kte], yte[kte]
    return eog_regress_global(Xtr, Otr), eog_regress_global(Xte, Ote), ytr, yte


# ═══════════════════════════════════════════════════════════
def main():
    subs = list(range(1, 10))
    NCALS = [5, 10, 20, 40, 80, 160, None]

    print("=" * 96)
    print("EA 落地性验证 + 进一步优化 | 9 被试 | cross 协议")
    print("=" * 96)
    print("装载数据 ...", flush=True)
    DATA = {}
    for s in subs:
        DATA[s] = load_subject(s)
        print(f"  B{s:02d} ok", end='', flush=True)
    print()

    # ── 实验 1：校准数据量 ──
    print("\n" + "=" * 96)
    print("实验 1  校准数据量敏感性（EA 白化用多少个试次估 R̄）")
    print("=" * 96)
    print(f"{'被试':<6}" + "".join(f"{('全部' if n is None else str(n)):>9}" for n in NCALS))
    print('-' * 96)
    acc1 = np.full((len(subs), len(NCALS)), np.nan)
    for si, s in enumerate(subs):
        Xtr, Xte, ytr, yte = DATA[s]
        line = f"B{s:02d}   "
        for ni, n in enumerate(NCALS):
            a = pipeline(BANDS6, Xtr, Xte, ytr, yte, n_cal=n)
            acc1[si, ni] = a
            line += f"{a*100:8.2f}%"
        print(line, flush=True)
    m1 = np.nanmean(acc1, 0)
    print('-' * 96)
    print(f"{'平均':<6}" + "".join(f"{v*100:8.2f}%" for v in m1))
    print(f"{'kappa':<6}" + "".join(f"{2*v-1:9.3f}" for v in m1))
    print(f"\n判读：5 试次 = {m1[0]*100:.2f}%，全部 = {m1[-1]*100:.2f}%")
    print(f"      20 试次（约 1 分钟任务）保住 {(m1[2]-m1[-1])*100:+.2f} pp 中的 "
          f"{max(0,(m1[2]-m1[4]))/(max(1e-9,m1[-1]-m1[4]))*100:.0f}% 增益（相对无 EA 见下）")

    # ── 实验 2：EA 均值类型 ──
    print("\n" + "=" * 96)
    print("实验 2  EA 变体：算术均值 vs 黎曼均值")
    print("=" * 96)
    acc2 = np.full((len(subs), 3), np.nan)   # 0=无EA 1=arith 2=riemann
    for si, s in enumerate(subs):
        Xtr, Xte, ytr, yte = DATA[s]
        acc2[si, 0] = pipeline(BANDS6, Xtr, Xte, ytr, yte, do_ea=False)
        acc2[si, 1] = pipeline(BANDS6, Xtr, Xte, ytr, yte, ea_mode="arith")
        acc2[si, 2] = pipeline(BANDS6, Xtr, Xte, ytr, yte, ea_mode="riemann")
        print(f"B{s:02d}   无EA {acc2[si,0]*100:6.2f}%  算术 {acc2[si,1]*100:6.2f}%  "
              f"黎曼 {acc2[si,2]*100:6.2f}%", flush=True)
    m2 = np.nanmean(acc2, 0)
    print('-' * 96)
    print(f"{'平均':<6}无EA {m2[0]*100:6.2f}%  算术 {m2[1]*100:6.2f}% ({(m2[1]-m2[0])*100:+.2f})  "
          f"黎曼 {m2[2]*100:6.2f}% ({(m2[2]-m2[0])*100:+.2f})")

    # ── 实验 3：超参数 ──
    print("\n" + "=" * 96)
    print("实验 3  超参数：特征选择 k / CSP m")
    print("=" * 96)
    KS = [4, 6, 8, 12]
    MS = [1, 2]
    acc3 = np.full((len(subs), len(KS) * len(MS)), np.nan)
    cols = [(k, m) for m in MS for k in KS]
    for si, s in enumerate(subs):
        Xtr, Xte, ytr, yte = DATA[s]
        for ci, (k, m) in enumerate(cols):
            acc3[si, ci] = pipeline(BANDS6, Xtr, Xte, ytr, yte, m=m, k=k)
    m3 = np.nanmean(acc3, 0)
    print(f"{'':<8}" + "".join(f"{f'k={k}':>10}" for k in KS))
    for mi_, m in enumerate(MS):
        print(f"{f'm={m}':<8}" + "".join(f"{m3[mi_*len(KS)+ki]*100:9.2f}%" for ki in range(len(KS))))
    best = cols[int(np.nanargmax(m3))]
    print(f"\n最优: k={best[0]}, m={best[1]}  →  {np.nanmax(m3)*100:.2f}%")

    # ── 实验 4：子带数 ──
    print("\n" + "=" * 96)
    print("实验 4  子带配置")
    print("=" * 96)
    acc4 = np.full((len(subs), 3), np.nan)
    for si, s in enumerate(subs):
        Xtr, Xte, ytr, yte = DATA[s]
        acc4[si, 0] = pipeline(BANDS4, Xtr, Xte, ytr, yte, k=6)
        acc4[si, 1] = pipeline(BANDS6, Xtr, Xte, ytr, yte, k=8)
        acc4[si, 2] = pipeline(BANDS9, Xtr, Xte, ytr, yte, k=8)
        print(f"B{s:02d}   4带 {acc4[si,0]*100:6.2f}%  6带 {acc4[si,1]*100:6.2f}%  "
              f"9带 {acc4[si,2]*100:6.2f}%", flush=True)
    m4 = np.nanmean(acc4, 0)
    print('-' * 96)
    print(f"{'平均':<6}4带 {m4[0]*100:6.2f}%   6带 {m4[1]*100:6.2f}%   9带 {m4[2]*100:6.2f}%")

    np.savez(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "params", "optimize_v2.npz"),
             acc1=acc1, acc2=acc2, acc3=acc3, acc4=acc4,
             ncal=np.array([n if n else 0 for n in NCALS]))


if __name__ == "__main__":
    main()
