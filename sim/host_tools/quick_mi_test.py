#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""quick_mi_test.py —— 一组（20 trial）快速自检：数据质量 + 单组 LOO 识别

用途:
  只采 1 组（20 trial，左右各 10）做"链路/信号能不能分左右手"的快速验证，
  不要指望这 20 个试次训练出正式模型（样本太少，准确率波动很大）。

用法:
  python sim/host_tools/quick_mi_test.py --bin data/raw/openbci/<日期>/xxx_raw.bin --events ..._events.csv
  python sim/host_tools/quick_mi_test.py --bin ... --events ... --channels 3,4      # 只用 C3/C4
  python sim/host_tools/quick_mi_test.py --bin ... --events ... --perm 100          # 置换检验次数

输出:
  · 滤波后数据质量（8-30Hz RMS / 50Hz / 通道相关）
  · 留一交叉验证（LOO）准确率 + 95% 置信区间 + 置换检验 p 值
  · 结论：是否明显高于随机（p<0.05 且 acc>~65% 才值得采正式 60 trial）
"""
import argparse, csv, os, sys
import numpy as np
from scipy import signal

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
from eeg_quality_check import parse_bin, notch_hp, band_rms, p50_ratio, table, NAMES  # noqa

FS = 250.0
WIN = 626          # 2.5 s 分析窗（cue + 0.5 .. 3.0 s）
OFFSET = 125       # cue + 0.5 s


def load_sos():
    """优先用项目导出的 3 子带 SOS（与 MATLAB 训练一致），否则用 butter 兜底。"""
    p = os.path.join(ROOT, "matlab", "data", "fbcsp_filters.mat")
    if os.path.isfile(p):
        from scipy.io import loadmat
        F = loadmat(p)
        sos = np.ascontiguousarray(np.asarray(F["sos3"], dtype=float))
        return [np.ascontiguousarray(s) for s in sos]
    return [signal.butter(2, [lo / 125, hi / 125], btype="band", output="sos")
            for lo, hi in ((8, 16), (16, 24), (24, 32))]


def load_events(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    cue, lab, blk = {}, {}, {}
    for r in rows:
        if r.get("event") == "cue" and r.get("trial") != "":
            t = int(r["trial"])
            cue[t] = int(r["sample"]); lab[t] = int(r["label"]); blk[t] = int(r["block"])
    trials = sorted(cue)
    return trials, cue, lab, blk


def build_windows(X, trials, cue):
    """返回 (n_trials, n_ch, WIN) 与有效 trial 列表；X 为连续 uV (N,8)。"""
    out, keep = [], []
    for t in trials:
        s = cue[t] + OFFSET
        if s + WIN > X.shape[0]:
            continue
        out.append(X[s:s + WIN, :].T)
        keep.append(t)
    return np.stack(out), keep


def bandpass_windows(Xw, sos_list):
    """(n,nch,T) -> (n,nch,T,nB)，因果滤波 + 每窗每通道去均值。"""
    n, ch, T = Xw.shape
    B = len(sos_list)
    out = np.zeros((n, ch, T, B))
    for b in range(B):
        Y = signal.sosfilt(sos_list[b], np.ascontiguousarray(Xw, dtype=float), axis=2)
        out[:, :, :, b] = Y - Y.mean(axis=2, keepdims=True)
    return out


def ea_matrix(Xb):
    """Xb: (n,ch,T) -> 白化矩阵 M = Rbar^(-1/2)（迹归一化平均协方差）。"""
    R = np.einsum("nct,ndt->cd", Xb, Xb) / (Xb.shape[0] * Xb.shape[2])
    R = R / np.trace(R)
    R = (R + R.T) / 2
    D, U = np.linalg.eigh(R)
    D = np.maximum(D, 1e-12)
    return U @ np.diag(D ** -0.5) @ U.T


def csp_fit(Xb, y, m=1):
    """Xb: (n,ch,T) 已对齐；返回 (ch, 2m) CSP 滤波器。"""
    ch, T = Xb.shape[1], Xb.shape[2]
    R = np.zeros((2, ch, ch))
    for c in (0, 1):
        Xc = Xb[y == c]
        Xc = Xc - Xc.mean(axis=2, keepdims=True)
        R[c] = np.einsum("nct,ndt->cd", Xc, Xc) / max(len(Xc), 1)
        R[c] /= max(np.trace(R[c]), 1e-30)
    C = R[0] + R[1]; C = (C + C.T) / 2
    D, U = np.linalg.eigh(C); D = np.maximum(D, 1e-12)
    P = U @ np.diag(D ** -0.5) @ U.T
    S1 = P @ R[0] @ P.T; S1 = (S1 + S1.T) / 2
    ev, V = np.linalg.eigh(S1)
    V = V[:, np.argsort(ev)[::-1]]
    W = P.T @ V
    return W[:, np.r_[0:m, ch - m:ch]]


def features(Xw_band, Weff):
    """Xw_band: (n,ch,T)；Weff: (ch,nc)；返回 (n,nc) 归一化对数方差。"""
    Z = np.einsum("kc,nct->nkt", Weff.T, np.ascontiguousarray(Xw_band))
    v = Z.var(axis=2, ddof=1)
    return np.log(v / v.sum(axis=1, keepdims=True))


def loo_accuracy(Data, y, sos_list, m=1):
    """Data: (n,ch,T,B) 已带通；留一 CV（每折在训练集上做 EA + CSP + 标准化 + SVM）。"""
    from sklearn.svm import SVC
    n, ch, T, B = Data.shape
    acc = np.zeros(n, dtype=bool)
    for i in range(n):
        tr = np.ones(n, dtype=bool); tr[i] = False
        Ftr, Fte = [], []
        for b in range(B):
            Xb = Data[:, :, :, b]
            Xtr = Xb[tr]
            M = ea_matrix(Xtr)
            Xa = np.einsum("cd,ndt->nct", M, Xtr)
            W = csp_fit(Xa, y[tr], m)
            Weff = M.T @ W
            Ftr.append(features(Xb[tr], Weff))
            Fte.append(features(Xb[i:i + 1], Weff))
        Ftr = np.concatenate(Ftr, axis=1)
        Fte = np.concatenate(Fte, axis=1)
        mu, sd = Ftr.mean(0), Ftr.std(0) + 1e-12
        clf = SVC(kernel="linear", C=1.0)
        clf.fit((Ftr - mu) / sd, y[tr])
        acc[i] = clf.predict((Fte - mu) / sd)[0] == y[i]
    return acc.mean()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--channels", default="", help="1 基通道号，逗号分隔；默认全部 8 路")
    ap.add_argument("--perm", type=int, default=60, help="置换检验次数（0=跳过）")
    a = ap.parse_args()

    print("=" * 70)
    print("20-trial 快速自检（单组 MI 数据）")
    print("=" * 70)
    X, dropped = parse_bin(a.bin)
    print("数据: %s  (%.1f s, %d 通道, 丢字节 %d)" % (a.bin, X.shape[0] / FS, X.shape[1], dropped))

    trials, cue, lab, blk = load_events(a.events)
    y_all = np.array([lab[t] for t in trials])
    print("事件: %d trial（左手 %d / 右手 %d），组: %s" % (
        len(trials), (y_all == 0).sum(), (y_all == 1).sum(), sorted(set(blk.values()))))

    ch_idx = list(range(8)) if not a.channels else [int(x) - 1 for x in a.channels.split(",")]
    print("使用通道:", ", ".join(NAMES[c] for c in ch_idx))

    # 质量（滤波后口径）
    Xf = notch_hp(X)
    Xs = Xf[:, ch_idx]
    rms = band_rms(Xs, 8, 30)
    r50 = np.median(p50_ratio(Xs))
    b6, a6 = signal.butter(6, [8 / 125, 30 / 125], btype="band")
    Yb = signal.lfilter(b6, a6, Xs - Xs.mean(0), axis=0)
    C = np.corrcoef(Yb.T)
    corr = (C.sum() - min(8, C.shape[0])) / (C.shape[0] * (C.shape[0] - 1)) \
        if C.shape[0] > 1 else 0.0
    q_ok = (np.median(rms) < 40) and (corr < 0.95)
    print("质量(滤波后): 8-30Hz RMS 中位数 %.1f uV（目标<40） | 通道相关 %.2f（目标<0.95） | 50Hz 中位突出 %.0fx"
          % (np.median(rms), corr, r50))
    print("质量判定:", "PASS（可以继续看识别）" if q_ok else "FAIL（先修连接/等电极稳定，再看识别也没意义）")

    # 窗口 + 带通
    Xw, keep = build_windows(X, trials, cue)
    y = np.array([lab[t] for t in keep])
    print("\n有效试次: %d（左手 %d / 右手 %d）" % (len(keep), (y == 0).sum(), (y == 1).sum()))
    if len(keep) < 10:
        print("试次太少，退出。"); return 1
    Xw = Xw[:, ch_idx, :]
    sos_list = load_sos()
    Data = bandpass_windows(Xw, sos_list)

    # LOO 准确率 + 置换检验
    acc = loo_accuracy(Data, y, sos_list)
    n = len(y)
    se = np.sqrt(acc * (1 - acc) / n) if 0 < acc < 1 else 0
    ci = 1.96 * max(se, 1 / (2 * np.sqrt(n))) if n else 0
    kappa = 2 * acc - 1
    print("\n留一交叉验证（LOO）: acc = %.1f%% (%d/%d), kappa = %+.3f, 95%% CI ≈ ±%.1f%%"
          % (100 * acc, round(acc * n), n, kappa, 100 * ci))

    pval = float("nan")
    if a.perm > 0:
        rng = np.random.default_rng(42)
        accs = []
        for _ in range(a.perm):
            yp = rng.permutation(y)
            accs.append(loo_accuracy(Data, yp, sos_list))
        accs = np.array(accs)
        pval = (1 + np.sum(accs >= acc)) / (1 + len(accs))
        print("置换检验（%d 次打乱标签）: chance acc = %.1f%% ± %.1f, p = %.3f"
              % (a.perm, 100 * accs.mean(), 100 * accs.std(), pval))

    print("\n结论:")
    if not q_ok:
        print("  数据质量不合格 → 这个准确率没有参考价值，先重采。（见上方质量判定）")
    elif pval < 0.05 and acc > 0.65:
        print("  明显高于随机（p=%.3f）→ 信号里有左右手信息，值得采正式 60 trial 训练。")
    elif acc > 0.60:
        print("  略高于随机但样本太少，不能下结论 → 建议采 60 trial 再做正式评估。")
    else:
        print("  与随机无显著差异 → 可能信号还没稳定/被试 MI 不敏感；先重采或换被试。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
