#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FBCSP 之上的优化项验证 —— 在 BCI IV 2b 全部 9 被试上跑 cross 协议

测试四个改进方向（全部离线，PL 侧零改动或可折叠）：
  EA   : 欧氏对齐（Euclidean Alignment）—— 白化可折进 CSP 矩阵
  EOG  : 眼电回归剔除 —— 需要 EOG 通道，所以直接读 GDF
  MW   : 多时间窗集成
  COMB : 以上组合

基线（已在 MATLAB 测得，此处用同一流程复现）：
  CSP1 + SVM  74.39%   |   FBCSP6 + SVM  77.40%   |   FBCSP6 + NBPW  77.88%

用法: python optimize_2b.py [--subjects 1 2 ...]
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

import mne  # noqa: E402
from scipy import signal  # noqa: E402
from scipy.io import loadmat  # noqa: E402

mne.set_log_level("ERROR")

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "..", "data", "raw")
GDF = os.path.join(RAW, "BCICIV_2b_gdf")
LAB = os.path.join(RAW, "BCICIV_2b_true_labels")

FS = 250.0
SESS_T = ["01T", "02T", "03T"]
SESS_E = ["04E", "05E"]
T_EXPORT = (2.5, 7.0)
EEG_CH = ["EEG:C3", "EEG:Cz", "EEG:C4"]
EOG_CH = ["EOG:ch01", "EOG:ch02", "EOG:ch03"]

# 6 带配置（与 export_fbcsp_filters.py 的 sos6 一致）
BANDS6 = [(8, 12), (12, 16), (16, 20), (20, 24), (24, 28), (28, 32)]
WINDOWS = [(2.5, 4.5), (3.5, 5.5), (4.5, 6.5)]     # 多窗用；单窗用中间那个


def cheby2_sos(lo, hi, fs=FS, order=5, rs=40):
    N, Wn = signal.cheb2ord([lo, hi], [lo - 3, hi + 3], 0.5, rs, fs=fs)
    return signal.cheby2(N, rs, Wn, btype="band", fs=fs, output="sos")


def butter_sos(lo, hi, fs=FS, order=2):
    return signal.butter(order, [lo, hi], btype="band", fs=fs, output="sos")


# ═══════════════════════════════════════════════════════════
#  数据装载
# ═══════════════════════════════════════════════════════════
def load_session(subj, sess):
    """返回 (eeg (n,3,T) µV, eog (n,3,T) µV, y, keep) —— 未滤波"""
    path = os.path.join(GDF, f"B{subj:02d}{sess}.gdf")
    raw = mne.io.read_raw_gdf(path, preload=True, verbose="ERROR")
    eeg = raw.copy().pick(EEG_CH)
    eog = raw.copy().pick(EOG_CH)

    events, emap = mne.events_from_annotations(raw, verbose="ERROR")

    def code(name):
        h = [v for k, v in emap.items() if name in str(k)]
        return h[0] if h else None

    c_start, c_l, c_r, c_rej = code("768"), code("769"), code("770"), code("1023")
    starts = events[events[:, 2] == c_start]
    n = len(starts)

    y = None
    if c_l is not None and c_r is not None:
        cue = events[np.isin(events[:, 2], [c_l, c_r])]
        if len(cue) == n:
            y = np.where(cue[:, 2] == c_l, 0, 1)
    if y is None:
        y = np.asarray(loadmat(os.path.join(LAB, f"B{subj:02d}{sess}.mat"),
                               squeeze_me=True)["classlabel"]).ravel()
        y = np.where(y == 1, 0, 1)
    assert len(y) == n

    kw = dict(tmin=T_EXPORT[0], tmax=T_EXPORT[1], baseline=None,
              preload=True, verbose="ERROR")
    Xe = mne.Epochs(eeg, starts, event_id={"t": c_start}, **kw).get_data() * 1e6
    Xo = mne.Epochs(eog, starts, event_id={"t": c_start}, **kw).get_data() * 1e6

    keep = np.ones(n, bool)
    if c_rej is not None:
        rej = raw.annotations.onset[[("1023" in str(d)) for d in raw.annotations.description]]
        for i, ev in enumerate(starts):
            a, b = ev[0] / FS + T_EXPORT[0], ev[0] / FS + T_EXPORT[1]
            if np.any((rej >= a) & (rej <= b)):
                keep[i] = False
    return Xe, Xo, y.astype(int), keep


# ═══════════════════════════════════════════════════════════
#  预处理算子
# ═══════════════════════════════════════════════════════════
def eog_regress(Xe, Xo):
    """逐试次把 EEG 对 3 路 EOG 做线性回归并减去拟合部分（Gratton 法）"""
    n, C, T = Xe.shape
    A = np.concatenate([Xo, np.ones((n, 1, T))], axis=1)      # (n, 4, T)
    A = A.transpose(0, 2, 1)                                   # (n, T, 4)
    Y = Xe.transpose(0, 2, 1)                                  # (n, T, C)
    # 最小二乘：B = (A^T A)^-1 A^T Y
    AtA = np.einsum("nti,ntj->nij", A, A)
    AtY = np.einsum("nti,ntc->nic", A, Y)
    AtA += np.eye(A.shape[2])[None] * 1e-8 * np.trace(AtA, axis1=1, axis2=2)[:, None, None] / A.shape[2]
    B = np.linalg.solve(AtA, AtY)                              # (n, 4, C)
    fit = np.einsum("nti,nic->ntc", A, B)
    return (Y - fit).transpose(0, 2, 1)


def dc_highpass(X, fc=0.5, fs=FS):
    a = 1.0 / (1.0 + 2 * np.pi * fc / fs)
    return signal.lfilter([a, -a], [1, -a], X, axis=2)


def band_filter(X, sos):
    Xf = signal.sosfilt(sos, X, axis=2)
    return signal.sosfilt(sos, Xf[:, :, ::-1], axis=2)[:, :, ::-1]   # 零相位


def crop(X, tmin, tmax):
    i0 = int(round((tmin - T_EXPORT[0]) * FS))
    i1 = i0 + int(round((tmax - tmin) * FS)) + 1
    Xc = X[:, :, i0:i1]
    return Xc - Xc.mean(axis=2, keepdims=True)


def euclid_align(X):
    """欧氏对齐：X̃ = R̄^(-1/2) X，R̄ 为试次平均协方差（迹归一化后）"""
    n, C, T = X.shape
    R = np.zeros((C, C))
    for i in range(n):
        Xi = X[i]
        R += Xi @ Xi.T / np.trace(Xi @ Xi.T + 1e-30)
    R /= n
    d, U = np.linalg.eigh((R + R.T) / 2)
    d = np.maximum(d, 1e-12)
    M = U @ np.diag(d ** -0.5) @ U.T
    return np.einsum("ij,njt->nit", M, X), M


# ═══════════════════════════════════════════════════════════
#  CSP / 特征 / 分类
# ═══════════════════════════════════════════════════════════
def _lw(R):
    p, _, n = R.shape
    S = R.mean(2)
    mu = np.trace(S) / p
    d2 = np.linalg.norm(S - mu * np.eye(p), "fro") ** 2 / p
    bb = sum(np.linalg.norm(R[:, :, i] - S, "fro") ** 2 for i in range(n))
    s = min(bb / (n ** 2) / p, d2) / max(d2, 1e-30)
    return (1 - s) * S + s * mu * np.eye(p)


def csp_fit(X, y, m=2):
    n, C, T = X.shape
    R = np.zeros((C, C, n))
    for i in range(n):
        Xi = X[i]
        S = Xi @ Xi.T
        R[:, :, i] = S / max(np.trace(S), 1e-30)
    Cs = np.stack([_lw(R[:, :, y == c]) for c in (0, 1)], 2)
    Cc = Cs[:, :, 0] + Cs[:, :, 1]
    Cc = (Cc + Cc.T) / 2
    d, U = np.linalg.eigh(Cc)
    d = np.maximum(d, 1e-12)
    P = U @ np.diag(d ** -0.5) @ U.T
    S1 = P @ Cs[:, :, 0] @ P.T
    S1 = (S1 + S1.T) / 2
    ev, V = np.linalg.eigh(S1)
    V = V[:, np.argsort(ev)[::-1]]
    Wall = P.T @ V
    sel = np.r_[0:m, C - m:C] if 2 * m <= C else np.arange(C)
    return Wall[:, sel]


def csp_feat(X, W):
    """W: (n_ch, n_comp)，其**列**是空间滤波器 → z = W^T x
    ★ 必须是 W^T x，不是 W x。前者用 filter 矩阵，后者用 pattern 矩阵，是错的。"""
    Z = np.einsum("cj,nct->njt", W, X)          # Σ_c W[c,j]·x[n,c,t]
    v = np.maximum(Z.var(axis=2), 1e-30)
    return np.log(v / v.sum(axis=1, keepdims=True))


def nbpw(Xtr, ytr, Xte, sigma=1.0):
    cls = np.unique(ytr)
    lp = np.zeros((len(Xte), len(cls)))
    for ci, c in enumerate(cls):
        Xc = Xtr[ytr == c]
        acc = np.zeros(len(Xte))
        for d in range(Xte.shape[1]):
            K = np.exp(-0.5 * ((Xte[:, d, None] - Xc[None, :, d]) / sigma) ** 2)
            acc += np.log(K.mean(1) / (sigma * np.sqrt(2 * np.pi)) + 1e-300)
        lp[:, ci] = acc + np.log(len(Xc) / len(Xtr))
    return cls[np.argmax(lp, 1)]


def svm_fit_predict(Ztr, ytr, Zte, C=1.0):
    from sklearn.svm import SVC
    clf = SVC(kernel="linear", C=C).fit(Ztr, ytr)
    return clf.predict(Zte)


# ═══════════════════════════════════════════════════════════
#  一条完整配置的评估
# ═══════════════════════════════════════════════════════════
def prep_bands(X, use_ea, t0, t1, pre=None):
    """对一个域：逐带带通 → 裁剪到窗 →（可选）欧氏对齐
    pre 若给定，则是预先在完整时长上滤好的 6 个带，避免多窗重复滤波"""
    out = []
    for bi, (lo, hi) in enumerate(BANDS6):
        Xf = pre[bi] if pre is not None else band_filter(X, butter_sos(lo, hi))
        Xc = crop(Xf, t0, t1)
        if use_ea:
            Xc, _ = euclid_align(Xc)
        out.append(Xc)
    return out


def run_config(Xtr, Xte, ytr, yte, cfg, pretr=None, prete=None):
    """cfg: dict(use_eog, use_ea, windows, clf, k_select, m)"""
    Ftr_parts, Fte_parts = [], []
    for (t0, t1) in cfg["windows"]:
        Btr = prep_bands(Xtr, cfg["use_ea"], t0, t1, pretr)
        Bte = prep_bands(Xte, cfg["use_ea"], t0, t1, prete)
        for i in range(len(Btr)):
            # ★ CSP 只用训练集拟合，测试集复用同一个 W（避免信息泄漏）
            W = csp_fit(Btr[i], ytr, cfg["m"])
            Ftr_parts.append(csp_feat(Btr[i], W))
            Fte_parts.append(csp_feat(Bte[i], W))
    Ftr = np.hstack(Ftr_parts)
    Fte = np.hstack(Fte_parts)

    # MIBIF（只用训练集）
    from sklearn.feature_selection import mutual_info_classif
    mi = mutual_info_classif(Ftr, ytr, random_state=0)
    k = min(cfg["k_select"], Ftr.shape[1])
    idx = np.argsort(mi)[::-1][:k]
    Ftr, Fte = Ftr[:, idx], Fte[:, idx]

    mu, sd = Ftr.mean(0), Ftr.std(0)
    sd[sd < 1e-12] = 1
    Ztr, Zte = (Ftr - mu) / sd, (Fte - mu) / sd

    if cfg["clf"] == "nbpw":
        yh = nbpw(Ztr, ytr, Zte, cfg.get("sigma", 1.0))
    else:
        yh = svm_fit_predict(Ztr, ytr, Zte)
    return float((yh == yte).mean())


# ═══════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subjects", type=int, nargs="*", default=list(range(1, 10)))
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--m", type=int, default=2)
    a = ap.parse_args()

    CFGS = [
        ("A  单带 CSP1 + SVM       ", dict(use_eog=0, use_ea=0, windows=[(3.5, 6.0)], clf="svm", k_select=3, m=2)),
        ("B  FBCSP6 + SVM          ", dict(use_eog=0, use_ea=0, windows=[(3.5, 6.0)], clf="svm", k_select=a.k, m=a.m)),
        ("C  FBCSP6 + EA + SVM     ", dict(use_eog=0, use_ea=1, windows=[(3.5, 6.0)], clf="svm", k_select=a.k, m=a.m)),
        ("D  FBCSP6 + EOG回归 + SVM ", dict(use_eog=1, use_ea=0, windows=[(3.5, 6.0)], clf="svm", k_select=a.k, m=a.m)),
        ("E  FBCSP6 + EA + EOG + SVM", dict(use_eog=1, use_ea=1, windows=[(3.5, 6.0)], clf="svm", k_select=a.k, m=a.m)),
        ("F  FBCSP6 + 多窗 + SVM    ", dict(use_eog=0, use_ea=0, windows=WINDOWS, clf="svm", k_select=a.k * 2, m=a.m)),
        ("G  E + F 全开 + SVM       ", dict(use_eog=1, use_ea=1, windows=WINDOWS, clf="svm", k_select=a.k * 2, m=a.m)),
        ("H  FBCSP6 + EA + NBPW     ", dict(use_eog=0, use_ea=1, windows=[(3.5, 6.0)], clf="nbpw", k_select=a.k, m=a.m)),
    ]

    print("=" * 100)
    print("FBCSP 优化项验证 | BCI IV 2b | cross 协议 (01T+02T+03T → 04E+05E)")
    print("=" * 100)

    res = np.full((len(a.subjects), len(CFGS)), np.nan)
    for si, s in enumerate(a.subjects):
        try:
            Xe_t, Xo_t, y_t, k_t = [], [], [], []
            for sess in SESS_T:
                e, o, y, kp = load_session(s, sess)
                Xe_t.append(e); Xo_t.append(o); y_t.append(y); k_t.append(kp)
            Xe_e, Xo_e, y_e, k_e = [], [], [], []
            for sess in SESS_E:
                e, o, y, kp = load_session(s, sess)
                Xe_e.append(e); Xo_e.append(o); y_e.append(y); k_e.append(kp)

            Xtr = np.concatenate(Xe_t); Xte = np.concatenate(Xe_e)
            Otr = np.concatenate(Xo_t); Ote = np.concatenate(Xo_e)
            ytr = np.concatenate(y_t);  yte = np.concatenate(y_e)
            ktr = np.concatenate(k_t);  kte = np.concatenate(k_e)
            Xtr, Otr, ytr = Xtr[ktr], Otr[ktr], ytr[ktr]
            Xte, Ote, yte = Xte[kte], Ote[kte], yte[kte]

            # 去 DC（共通）
            Xtr, Xte = dc_highpass(Xtr), dc_highpass(Xte)
            Otr, Ote = dc_highpass(Otr), dc_highpass(Ote)
            # EOG 回归（如果该配置需要）
            XtrE = eog_regress(Xtr, Otr)
            XteE = eog_regress(Xte, Ote)

            # 预先在完整时长上滤好 6 个带（多窗配置复用，避免重复滤波）
            def pref(X):
                return [band_filter(X, butter_sos(lo, hi)) for lo, hi in BANDS6]
            pre_a, pre_b = pref(Xtr), pref(Xte)
            pre_aE, pre_bE = pref(XtrE), pref(XteE)

            line = f"B{s:02d}  "
            for ci, (name, cfg) in enumerate(CFGS):
                if cfg["use_eog"]:
                    Xa, Xb, pa, pb = XtrE, XteE, pre_aE, pre_bE
                else:
                    Xa, Xb, pa, pb = Xtr, Xte, pre_a, pre_b
                acc = run_config(Xa, Xb, ytr, yte, cfg, pa, pb)
                res[si, ci] = acc
                line += f"{acc*100:5.1f} "
            print(line, flush=True)
        except Exception as ex:
            print(f"B{s:02d}  失败: {type(ex).__name__}: {ex}")

    print("-" * 100)
    print(f"{'配置':<26}" + "".join(f"{s:>6}" for s in ["平均", "kappa", "vs B"]))
    print("-" * 100)
    base = np.nanmean(res[:, 1])
    for ci, (name, _) in enumerate(CFGS):
        m_ = np.nanmean(res[:, ci])
        kb = 2 * m_ - 1
        g = "" if ci == 1 else f"{(m_ - base)*100:+.2f} pp"
        print(f"{name:<26}{m_*100:6.2f}{kb:7.3f}   {g}")
    print("-" * 100)
    print("对照: BCI IV 2b 官方冠军 (FBCSP+NBPW) kappa +0.600")
    np.save(os.path.join(HERE, "..", "params", "optimize_results.npy"), res)


if __name__ == "__main__":
    main()
