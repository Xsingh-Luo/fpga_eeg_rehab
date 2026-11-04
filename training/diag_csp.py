#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定位 CSP 训练实现的差异：迹归一化 + 正则化方式"""
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
from viz_classification import DATA, cheby2_sos, csp_train, fit_linear_svm, preprocess  # noqa: E402
from scipy.io import loadmat  # noqa: E402
from mne.decoding import CSP  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.svm import SVC  # noqa: E402


def my_csp(X, y, m=2, trace_norm=True, reg='lw'):
    n, nch, T = X.shape
    R = np.zeros((nch, nch, n))
    for i in range(n):
        Xi = X[i] - X[i].mean(axis=1, keepdims=True)
        S = Xi @ Xi.T
        R[:, :, i] = S / np.trace(S) if trace_norm else S
    C = np.zeros((nch, nch, 2))
    for c in (0, 1):
        Rc = R[:, :, y == c]
        S = Rc.mean(axis=2)
        if reg == 'lw':
            p, _, nn = Rc.shape
            mu = np.trace(S) / p
            d2 = np.linalg.norm(S - mu * np.eye(p), 'fro') ** 2 / p
            bb = sum(np.linalg.norm(Rc[:, :, i] - S, 'fro') ** 2 for i in range(nn))
            s = min(bb / (nn ** 2) / p, d2) / max(d2, 1e-30)
            C[:, :, c] = (1 - s) * S + s * mu * np.eye(p)
        else:
            a = 0.05
            C[:, :, c] = (1 - a) * S + a * (np.trace(S) / nch) * np.eye(nch)
    Cc = C[:, :, 0] + C[:, :, 1]; Cc = (Cc + Cc.T) / 2
    d, U = np.linalg.eigh(Cc); d = np.maximum(d, 1e-30)
    P = U @ np.diag(1 / np.sqrt(d)) @ U.T
    S1 = P @ C[:, :, 0] @ P.T; S1 = (S1 + S1.T) / 2
    ev, V = np.linalg.eigh(S1)
    o = np.argsort(ev)[::-1]; V = V[:, o]
    Wall = P.T @ V
    sel = np.r_[0:m, nch - m:nch] if 2 * m <= nch else np.arange(nch)
    return Wall[:, sel]


def feats(X, W, normalize=True):
    Z = np.einsum('cj,nct->njt', W, X)   # z = W^T x
    v = np.maximum(Z.var(axis=2), 1e-30)
    return np.log(v / v.sum(axis=1, keepdims=True)) if normalize else np.log(v)


def acc_of(Ftr, ytr, Fte, yte):
    w, b, _ = fit_linear_svm(Ftr, ytr)
    return float((((Fte @ w + b) > 0).astype(int) == yte).mean())


def main():
    sos = cheby2_sos()
    subs = [1, 2, 5, 6, 9]
    res = {}
    print(f"{'被试':<6}{'我(迹归一)':>12}{'我(不归一)':>12}{'我(迹归一+固定α)':>18}"
          f"{'MNE(nt=F)':>12}{'MNE(nt=T)':>12}")
    print('-' * 76)
    for s in subs:
        D = loadmat(os.path.join(DATA, f'subj{s:02d}.mat'), squeeze_me=True, struct_as_record=False)
        meta = D['meta']
        if hasattr(meta, '_fieldnames'):
            meta = {k: getattr(meta, k) for k in meta._fieldnames}
        meta['win_export'] = [float(v) for v in np.atleast_1d(meta['win_export']).ravel()]
        ytr = np.asarray(D['ytr']).ravel().astype(int)
        yte = np.asarray(D['yte']).ravel().astype(int)
        Xtr = preprocess(D['Xtr'], meta, sos, True)
        Xte = preprocess(D['Xte'], meta, sos, True)

        a = acc_of(feats(Xtr, my_csp(Xtr, ytr, 2, True, 'lw')), ytr,
                   feats(Xte, my_csp(Xtr, ytr, 2, True, 'lw')), yte)
        b = acc_of(feats(Xtr, my_csp(Xtr, ytr, 2, False, 'lw')), ytr,
                   feats(Xte, my_csp(Xtr, ytr, 2, False, 'lw')), yte)
        c = acc_of(feats(Xtr, my_csp(Xtr, ytr, 2, True, 'fixed')), ytr,
                   feats(Xte, my_csp(Xtr, ytr, 2, True, 'fixed')), yte)

        e = {}
        for nt in (False, True):
            p = Pipeline([('csp', CSP(n_components=3, reg='ledoit_wolf', log=False,
                                      norm_trace=nt)),
                          ('svm', SVC(kernel='linear', C=1.0))])
            p.fit(Xtr, ytr)
            e[nt] = float(p.score(Xte, yte))
        res[s] = (a, b, c, e[False], e[True])
        print(f"B{s:02d}   {a*100:11.2f}%{b*100:11.2f}%{c*100:17.2f}%"
              f"{e[False]*100:11.2f}%{e[True]*100:11.2f}%")
    print('-' * 76)
    m = np.mean([res[s] for s in subs], axis=0)
    print(f"{'平均':<6}{m[0]*100:11.2f}%{m[1]*100:11.2f}%{m[2]*100:17.2f}%"
          f"{m[3]*100:11.2f}%{m[4]*100:11.2f}%")
    print()
    print("说明: MNE(nt=F) = norm_trace=False（baseline_2b.py 用的就是这个）")
    print("      MNE(nt=T) = norm_trace=True")
    print("      我(迹归一) = 每 trial 协方差除以迹后平均（经典 CSP，Ramoser 2000）")
    print("      我(不归一) = 不做迹归一化")


if __name__ == '__main__':
    main()
