#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CSP + SVM 分类过程可视化 —— 把「输入试次波形」与「分类结果」一一对应地画出来

数据与管线与 matlab/csp_svm_validate.m 完全一致（读同一份 int16 计数域 .mat），
所以图上标出的准确率与 MATLAB 报告里的数字能对上。

生成:
  fig_c1_单试次全链路.png    一个试次从原始波形到决策值的逐级分解
  fig_c2_试次波形与分类结果.png  ★ 核心图：16 个试次，每个画输入波形 + 分类结果
  fig_c3_特征空间.png        归一化对数方差特征散点 + 决策边界
  fig_c4_决策值分布.png      正确/错误试次的决策值分布
  fig_c5_CSP空间滤波器.png   CSP 学到的空间权重
  fig_c6_累积准确率.png      评价集上随试次推进的累积准确率

用法: python viz_classification.py [--subject 1] [--ntrials 16]
"""
import argparse
import os
import sys
import warnings

import numpy as np
from scipy import signal
from scipy.io import loadmat

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

# ── 中文字体 ──
for fp in (r"C:\WINDOWS\Fonts\msyh.ttc", r"C:\WINDOWS\Fonts\simhei.ttf"):
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 110
plt.rcParams["savefig.dpi"] = 160
plt.rcParams["savefig.bbox"] = "tight"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "matlab", "data")
OUT = os.path.join(HERE, "..", "规划", "figures")
os.makedirs(OUT, exist_ok=True)

CH = ["C3", "Cz", "C4"]
CH_COLOR = ["#1f77b4", "#d62728", "#2ca02c"]
CLS_NAME = {0: "左手想象", 1: "右手想象"}
FS = 250.0


# ═══════════════════════════════════════════════════════════
#  算法（与 MATLAB 版逐行对应）
# ═══════════════════════════════════════════════════════════
def cheby2_sos(band=(8, 30), stop=(5, 45), rp=0.5, rs=40, fs=FS):
    N, Wn = signal.cheb2ord(band, stop, rp, rs, fs=fs)
    return signal.cheby2(N, rs, Wn, btype="band", fs=fs, output="sos")


def preprocess(X, meta, sos, causal=True, tuse=(3.5, 6.0), dc_fc=0.5):
    """X:(n,nch,T) int16 计数 → 去DC → 带通 → 裁剪 → 每trial去均值"""
    fs, G = meta["fs"], float(meta["G"])
    X = np.asarray(X, dtype=np.float64) / G                     # → µV
    a = 1.0 / (1.0 + 2 * np.pi * dc_fc / fs)
    X = signal.lfilter([a, -a], [1, -a], X, axis=2)             # 一阶高通
    X = signal.sosfilt(sos, X, axis=2)                          # 带通
    if not causal:
        X = signal.sosfilt(sos, X[:, :, ::-1], axis=2)[:, :, ::-1]
    i0 = int(round((tuse[0] - meta["win_export"][0]) * fs))
    i1 = i0 + int(round((tuse[1] - tuse[0]) * fs)) + 1
    X = X[:, :, i0:i1]
    return X - X.mean(axis=2, keepdims=True)


def _lw_shrink(R):
    """Ledoit-Wolf 收缩（目标 = scaled identity）"""
    p, _, n = R.shape
    S = R.mean(axis=2)
    mu = np.trace(S) / p
    d2 = np.linalg.norm(S - mu * np.eye(p), "fro") ** 2 / p
    bb = sum(np.linalg.norm(R[:, :, i] - S, "fro") ** 2 for i in range(n))
    s = min(bb / (n ** 2) / p, d2) / max(d2, 1e-30)
    return (1 - s) * S + s * mu * np.eye(p)


def csp_train(X, y, m=2):
    n, nch, T = X.shape
    R = np.zeros((nch, nch, n))
    for i in range(n):
        Xi = X[i] - X[i].mean(axis=1, keepdims=True)
        S = Xi @ Xi.T
        R[:, :, i] = S / np.trace(S)
    C = np.stack([_lw_shrink(R[:, :, y == c]) for c in (0, 1)], axis=2)
    Cc = C[:, :, 0] + C[:, :, 1]
    Cc = (Cc + Cc.T) / 2
    d, U = np.linalg.eigh(Cc)
    d = np.maximum(d, 1e-30)
    P = U @ np.diag(1 / np.sqrt(d)) @ U.T
    S1 = P @ C[:, :, 0] @ P.T
    S1 = (S1 + S1.T) / 2
    ev, V = np.linalg.eigh(S1)
    ordr = np.argsort(ev)[::-1]
    V, ev = V[:, ordr], ev[ordr]
    Wall = P.T @ V
    sel = np.r_[0:m, nch - m:nch] if 2 * m <= nch else np.arange(nch)
    return Wall[:, sel], ev


def csp_features(X, W):
    Z = np.einsum("cj,nct->njt", W, X)   # z = W^T x
    v = Z.var(axis=2)
    v = np.maximum(v, 1e-30)
    return np.log(v / v.sum(axis=1, keepdims=True)), Z


def fit_linear_svm(F, y, C=1.0):
    from sklearn.svm import SVC
    clf = SVC(kernel="linear", C=C).fit(F, y)
    w = (clf.dual_coef_.T * clf.support_vectors_).sum(axis=0)
    b = float(clf.intercept_[0])
    return w, b, clf


# ═══════════════════════════════════════════════════════════
#  画图
# ═══════════════════════════════════════════════════════════
def fig1_pipeline(ctx, path):
    """单个试次的逐级分解"""
    Xr, Xf, Z, F, dec = ctx["Xraw1"], ctx["Xfilt1"], ctx["Z1"], ctx["F1"], ctx["dec1"]
    ytrue, ypred = ctx["ytrue1"], ctx["ypred1"]
    t_raw = np.arange(Xr.shape[1]) / FS + 2.5
    t_use = np.arange(Xf.shape[1]) / FS + 3.5

    fig, ax = plt.subplots(5, 1, figsize=(13, 13),
                           gridspec_kw={"height_ratios": [1.2, 1.2, 1, 1, 0.8]})

    def stack(axx, data, t, title, ylab):
        for c in range(3):
            off = c * np.percentile(np.abs(data), 99) * 2.6
            axx.plot(t, data[c] + off, color=CH_COLOR[c], lw=0.8)
            axx.text(t[-1], off, f" {CH[c]}", color=CH_COLOR[c],
                     fontsize=9, va="center", fontweight="bold")
        axx.set_title(title, fontsize=11, loc="left")
        axx.set_ylabel(ylab, fontsize=9)
        axx.set_yticks([])
        for s in ("top", "right", "left"):
            axx.spines[s].set_visible(False)
        axx.margins(x=0.02)

    stack(ax[0], Xr, t_raw, "① 原始输入（去DC/带通之前，计数域已还原为 µV）", "幅值")
    ax[0].axvspan(3.5, 6.0, color="orange", alpha=0.12)
    ax[0].text(4.75, ax[0].get_ylim()[1], " 分析窗 [3.5, 6.0] s", color="#E65100",
               fontsize=9, ha="center", va="top")

    stack(ax[1], Xf, t_use, "② 去DC(0.5 Hz 高通) + 带通 Cheby2 10 阶 8–30 Hz 后的信号", "幅值")

    for j in range(Z.shape[0]):
        ax[2].plot(t_use, Z[j] - j * np.percentile(np.abs(Z), 99) * 3.2,
                   lw=1.0, color=plt.cm.viridis(j / max(Z.shape[0] - 1, 1)))
        ax[2].text(t_use[-1], -j * np.percentile(np.abs(Z), 99) * 3.2,
                   f"  z{j+1}", fontsize=9, va="center",
                   color=plt.cm.viridis(j / max(Z.shape[0] - 1, 1)), fontweight="bold")
    ax[2].set_title("③ CSP 空间投影 z = W^Tx（每个分量是一组电极的加权组合）",
                    fontsize=11, loc="left")
    ax[2].set_ylabel("z", fontsize=9); ax[2].set_yticks([])
    for s in ("top", "right", "left"):
        ax[2].spines[s].set_visible(False)

    bars = ax[3].bar(np.arange(len(F)) + 1, F, color="#5C6BC0", width=0.5)
    for i, (bb, v) in enumerate(zip(bars, F)):
        ax[3].text(bb.get_x() + bb.get_width() / 2, v, f"  {v:.3f}",
                   ha="center", va="bottom" if v > np.mean(F) else "top", fontsize=9)
    ax[3].axhline(np.mean(F), color="gray", ls="--", lw=0.8)
    ax[3].set_title("④ 特征 f_i = log( var(z_i) / sum_j var(z_j) )   —— 对数方差，已归一化",
                    fontsize=11, loc="left")
    ax[3].set_xticks(np.arange(len(F)) + 1)
    ax[3].set_xticklabels([f"f{i+1}" for i in range(len(F))])
    ax[3].set_ylabel("特征值", fontsize=9)

    ax[4].axis("off")
    ok = ytrue == ypred
    ax[4].text(0.02, 0.72, f"⑤ SVM 决策：d = w·f + b = {dec:+.4f}", fontsize=13,
               fontweight="bold", transform=ax[4].transAxes)
    ax[4].text(0.02, 0.38,
               f"    真实标签 = {CLS_NAME[ytrue]}    预测标签 = {CLS_NAME[ypred]}    "
               f"{'√ 正确' if ok else '× 错误'}",
               fontsize=12, transform=ax[4].transAxes,
               color="#2E7D32" if ok else "#C62828", fontweight="bold")
    ax[4].text(0.02, 0.06,
               f"    判据: d > 0 → 右手想象,  d < 0 → 左手想象   "
               f"（|d| 越大越有把握）",
               fontsize=10, transform=ax[4].transAxes, color="#546E7A")
    ax[4].add_patch(plt.Rectangle((0.005, 0.0), 0.99, 0.95, transform=ax[4].transAxes,
                                  fill=True, facecolor="#E8F5E9" if ok else "#FFEBEE",
                                  edgecolor="#2E7D32" if ok else "#C62828",
                                  lw=2, zorder=-1))

    fig.suptitle(f"图 C1  单个试次的算法全链路（被试 B{ctx['subj']:02d}，试次 #{ctx['idx1']}）",
                 fontsize=15, fontweight="bold", y=0.995)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    fig.savefig(path); plt.close(fig)
    print(f"  -> {os.path.basename(path)}")


def fig2_trials(ctx, path, ntrials=16):
    """★ 核心图：每个试次的输入波形 ↔ 分类结果"""
    X, ytrue, ypred, dec = ctx["Xte"], ctx["yte"], ctx["ypred"], ctx["dec"]
    n = min(ntrials, len(ytrue))

    # 挑选：一半正确一半错误（若无足够错误则全取正确）
    err = np.where(ytrue != ypred)[0]
    ok = np.where(ytrue == ypred)[0]
    half = n // 2
    e_sel = err[:min(half, len(err))]
    o_sel = ok[:n - len(e_sel)]
    sel = np.r_[e_sel, o_sel]
    sel = sel[np.argsort(np.r_[np.zeros(len(e_sel)), np.ones(len(o_sel))])]  # 错误在前

    ncol = 4
    nrow = int(np.ceil(len(sel) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.1 * ncol, 2.5 * nrow))
    axes = np.atleast_2d(axes)
    t = np.arange(X.shape[2]) / FS + 3.5

    for k, idx in enumerate(sel):
        r, c = divmod(k, ncol)
        ax = axes[r, c]
        okk = ytrue[idx] == ypred[idx]
        ax.set_facecolor("#E8F5E9" if okk else "#FFEBEE")

        for ch in (2, 1, 0):                    # C4 在下、C3 在上，符合阅读习惯
            off = (2 - ch) * np.percentile(np.abs(X[idx]), 99) * 3.0
            ax.plot(t, X[idx, ch] + off, color=CH_COLOR[ch], lw=0.7)
        ax.set_yticks([]); ax.set_xticks([3.5, 4.5, 5.5, 6.0])
        ax.tick_params(labelsize=7)
        ax.set_xlim(t[0], t[-1])
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color("#BDBDBD")

        mark = "√" if okk else "×"
        col = "#2E7D32" if okk else "#C62828"
        ax.set_title(f"#{idx}  {mark}  真:{CLS_NAME[ytrue[idx]]}  预:{CLS_NAME[ypred[idx]]}\n"
                     f"d = {dec[idx]:+.3f}", fontsize=8.5, color=col,
                     fontweight="bold", pad=3)

    for k in range(len(sel), nrow * ncol):
        r, c = divmod(k, ncol)
        axes[r, c].axis("off")

    # 通道图例
    handles = [plt.Line2D([], [], color=CH_COLOR[i], lw=1.5, label=f"通道 {CH[i]}")
               for i in range(3)]
    handles += [Patch(facecolor="#E8F5E9", edgecolor="#2E7D32", label="分类正确"),
                Patch(facecolor="#FFEBEE", edgecolor="#C62828", label="分类错误")]
    fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False,
               fontsize=10, bbox_to_anchor=(0.5, -0.005))

    acc = (ytrue == ypred).mean()
    fig.suptitle(
        f"图 C2  试次输入波形与分类结果的一一对应（被试 B{ctx['subj']:02d}，评价集）\n"
        f"每格 = 一个试次的 3 通道带通信号（纵向偏移显示）+ 该试次的预测结果"
        f"　|　本图 {len(sel)} 个试次中错 {(ytrue[sel]!=ypred[sel]).sum()} 个"
        f"　|　全评价集准确率 {acc*100:.2f}%",
        fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0.03, 1, 0.93])
    fig.savefig(path); plt.close(fig)
    print(f"  -> {os.path.basename(path)}")


def fig3_feature_space(ctx, path):
    """特征空间 + 决策边界"""
    F, ytrue, ypred = ctx["Fte"], ctx["yte"], ctx["ypred"]
    w, b = ctx["w"], ctx["b"]
    ncomp = F.shape[1]
    pairs = [(0, 1)] if ncomp < 3 else [(0, 1), (0, 2), (1, 2)]

    fig, axes = plt.subplots(1, len(pairs), figsize=(5.2 * len(pairs), 4.8))
    axes = np.atleast_1d(axes)
    for ax, (i, j) in zip(axes, pairs):
        for cls in (0, 1):
            m = ytrue == cls
            ax.scatter(F[m, i], F[m, j], s=26, alpha=0.55,
                       c=("#1f77b4" if cls == 0 else "#d62728"),
                       label=f"真实 {CLS_NAME[cls]}", edgecolors="none")
        wrong = ytrue != ypred
        ax.scatter(F[wrong, i], F[wrong, j], s=110, facecolors="none",
                   edgecolors="black", linewidths=1.6, label="分类错误")

        # 决策边界在 (i,j) 平面的投影：w_i*f_i + w_j*f_j + (其余项均值) + b = 0
        rest = float(F[:, [k for k in range(ncomp) if k not in (i, j)]].mean(axis=0)
                     @ w[[k for k in range(ncomp) if k not in (i, j)]]) if ncomp > 2 else 0.0
        xs = np.linspace(F[:, i].min(), F[:, i].max(), 50)
        if abs(w[j]) > 1e-9:
            ys = -(w[i] * xs + rest + b) / w[j]
            ax.plot(xs, ys, "k--", lw=1.6, label="决策边界 d = 0")
        ax.set_xlabel(f"f{i+1} = log(var(z{i+1})/sumvar)", fontsize=10)
        ax.set_ylabel(f"f{j+1} = log(var(z{j+1})/sumvar)", fontsize=10)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8.5, loc="best")

    fig.suptitle("图 C3  归一化对数方差特征空间与线性决策边界\n"
                 "CSP 的目标就是让两类在这个空间里尽量分开",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.9])
    fig.savefig(path); plt.close(fig)
    print(f"  -> {os.path.basename(path)}")


def fig4_decision(ctx, path):
    """决策值分布"""
    dec, ytrue, ypred = ctx["dec"], ctx["yte"], ctx["ypred"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.6))

    ax = axes[0]
    bins = np.linspace(dec.min(), dec.max(), 41)
    for cls, col in ((0, "#1f77b4"), (1, "#d62728")):
        ax.hist(dec[ytrue == cls], bins=bins, alpha=0.65, color=col,
                label=f"真实 {CLS_NAME[cls]}", edgecolor="white", lw=0.4)
    ax.axvline(0, color="k", lw=2)
    ax.text(0, ax.get_ylim()[1] * 0.96, " 判据 d = 0", fontsize=10,
            fontweight="bold", va="top")
    wrong = ytrue != ypred
    ax.hist(dec[wrong], bins=bins, histtype="step", color="black", lw=1.8,
            label=f"分类错误（{wrong.sum()} 个）")
    ax.set_xlabel("SVM 决策值  d = w·f + b", fontsize=11)
    ax.set_ylabel("试次数", fontsize=11)
    ax.set_title("决策值分布：两类分得越开越好", fontsize=12)
    ax.legend(fontsize=9); ax.grid(alpha=0.25)

    ax = axes[1]
    order = np.argsort(dec)
    ax.plot(np.arange(len(dec)), dec[order], lw=1.2, color="#37474F")
    ax.axhline(0, color="k", lw=1.5)
    col = np.where(ytrue[order] == ypred[order], "#2E7D32", "#C62828")
    ax.scatter(np.arange(len(dec)), dec[order], c=col, s=14, zorder=3)
    ax.fill_between(np.arange(len(dec)), 0, dec[order],
                    where=dec[order] > 0, color="#d62728", alpha=0.12)
    ax.fill_between(np.arange(len(dec)), 0, dec[order],
                    where=dec[order] < 0, color="#1f77b4", alpha=0.12)
    ax.set_xlabel("试次（按决策值排序）", fontsize=11)
    ax.set_ylabel("决策值 d", fontsize=11)
    ax.set_title(f"排序后的决策值（绿=正确 {int((ytrue==ypred).sum())} 个，"
                 f"红=错误 {int(wrong.sum())} 个）", fontsize=12)
    ax.grid(alpha=0.25)

    fig.suptitle("图 C4  决策值分布与分类错误的关系", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.9])
    fig.savefig(path); plt.close(fig)
    print(f"  -> {os.path.basename(path)}")


def fig5_csp(ctx, path):
    """CSP 空间滤波器权重"""
    W = ctx["W"]
    nch, ncomp = W.shape
    fig, axes = plt.subplots(1, ncomp + 1, figsize=(4.0 * (ncomp + 1), 4.0))

    for j in range(ncomp):
        ax = axes[j]
        cols = ["#C62828" if v > 0 else "#1565C0" for v in W[:, j]]
        ax.bar(range(nch), W[:, j], color=cols, width=0.6)
        ax.axhline(0, color="k", lw=0.8)
        ax.set_xticks(range(nch)); ax.set_xticklabels(CH, fontsize=11)
        ax.set_title(f"滤波器 {j+1}\n（{'类1' if j < ncomp/2 else '类2'}方差占优）",
                     fontsize=10)
        ax.grid(alpha=0.25, axis="y")
        lim = np.abs(W).max() * 1.25
        ax.set_ylim(-lim, lim)
    axes[0].set_ylabel("空间权重", fontsize=11)

    ax = axes[-1]
    ev = ctx["eigvals"]
    ax.plot(np.arange(1, len(ev) + 1), ev, "o-", color="#5C6BC0", lw=1.5, ms=7)
    ax.set_xlabel("分量序号", fontsize=10); ax.set_ylabel("广义特征值", fontsize=10)
    ax.set_title("特征值谱\n首尾分量判别力最强", fontsize=10)
    ax.grid(alpha=0.25)

    fig.suptitle("图 C5  CSP 学到的空间滤波器 —— 每个滤波器就是一组电极权重\n"
                 "红=正权 蓝=负权；投影后让一类方差最大、另一类方差最小",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.87])
    fig.savefig(path); plt.close(fig)
    print(f"  -> {os.path.basename(path)}")


def fig6_running(ctx, path):
    """累积准确率"""
    ytrue, ypred = ctx["yte"], ctx["ypred"]
    correct = (ytrue == ypred).astype(float)
    run = np.cumsum(correct) / np.arange(1, len(correct) + 1)

    fig, ax = plt.subplots(figsize=(10, 4.4))
    ax.plot(np.arange(1, len(run) + 1), run * 100, lw=1.6, color="#1565C0")
    ax.axhline(correct.mean() * 100, color="#C62828", ls="--", lw=1.4,
               label=f"最终准确率 {correct.mean()*100:.2f}%")
    ax.axhline(50, color="gray", ls=":", lw=1.2, label="随机水平 50%")
    ax.fill_between(np.arange(1, len(run) + 1), 50, run * 100,
                    where=run * 100 > 50, color="#1565C0", alpha=0.10)
    ax.set_xlabel("已判别的试次数", fontsize=11)
    ax.set_ylabel("累积准确率 (%)", fontsize=11)
    ax.set_title(f"图 C6  评价集上的累积准确率（被试 B{ctx['subj']:02d}，共 {len(run)} 试次）\n"
                 "前几十个试次波动大是正常的 —— 样本少时统计噪声大",
                 fontsize=12, fontweight="bold")
    ax.legend(fontsize=10); ax.grid(alpha=0.25)
    ax.set_ylim(30, 100)
    fig.tight_layout()
    fig.savefig(path); plt.close(fig)
    print(f"  -> {os.path.basename(path)}")


# ═══════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", type=int, default=1)
    ap.add_argument("--ntrials", type=int, default=16)
    ap.add_argument("--causal", action="store_true", default=True)
    a = ap.parse_args()

    f = os.path.join(DATA, f"subj{a.subject:02d}.mat")
    if not os.path.exists(f):
        raise SystemExit(f"缺少 {f}，请先运行 python/export_2b_to_mat.py")
    D = loadmat(f, squeeze_me=True, struct_as_record=False)
    meta = D["meta"]
    if hasattr(meta, "_fieldnames"):                 # mat_struct → dict
        meta = {k: getattr(meta, k) for k in meta._fieldnames}
    meta["win_export"] = [float(v) for v in np.atleast_1d(meta["win_export"]).ravel()]

    sos = cheby2_sos()
    Xtr = preprocess(D["Xtr"], meta, sos, a.causal)
    Xte = preprocess(D["Xte"], meta, sos, a.causal)
    ytr = np.asarray(D["ytr"]).ravel().astype(int)
    yte = np.asarray(D["yte"]).ravel().astype(int)

    W, ev = csp_train(Xtr, ytr, m=2)
    Ftr, _ = csp_features(Xtr, W)
    Fte, Zte = csp_features(Xte, W)
    w, b, clf = fit_linear_svm(Ftr, ytr)

    dec = Fte @ w + b
    ypred = (dec > 0).astype(int)
    acc = (ypred == yte).mean()

    print(f"被试 B{a.subject:02d} | 训练 {len(ytr)} 试次 | 评价 {len(yte)} 试次")
    print(f"  评价集准确率 = {acc*100:.2f}%  (kappa {2*acc-1:+.3f})")
    print(f"  CSP 分量数 = {W.shape[1]}   支持向量 = {len(clf.support_vectors_)} "
          f"→ 折叠后只存 {len(w)} 个权重")
    print(f"  分类错误 {int((ypred!=yte).sum())} 个\n")

    # 选一个试次做全链路展示：优先挑一个决策值绝对值大的正确样本
    cand = np.where(ypred == yte)[0]
    idx1 = int(cand[np.argmax(np.abs(dec[cand]))])
    ctx = dict(
        subj=a.subject, W=W, eigvals=ev, w=w, b=b,
        Xte=Xte, yte=yte, ypred=ypred, dec=dec, Fte=Fte, Ftr=Ftr,
        Xraw1=np.asarray(D["Xte"][idx1], dtype=float) / float(meta["G"]),
        Xfilt1=Xte[idx1], Z1=Zte[idx1], F1=Fte[idx1],
        dec1=float(dec[idx1]), ytrue1=int(yte[idx1]), ypred1=int(ypred[idx1]),
        idx1=idx1,
    )

    print("生成图 ...")
    tag = f"B{a.subject:02d}"
    fig1_pipeline(ctx, os.path.join(OUT, f"figC1_单试次全链路_{tag}.png"))
    fig2_trials(ctx, os.path.join(OUT, f"figC2_试次波形与分类结果_{tag}.png"), a.ntrials)
    fig3_feature_space(ctx, os.path.join(OUT, f"figC3_特征空间_{tag}.png"))
    fig4_decision(ctx, os.path.join(OUT, f"figC4_决策值分布_{tag}.png"))
    fig5_csp(ctx, os.path.join(OUT, f"figC5_CSP空间滤波器_{tag}.png"))
    fig6_running(ctx, os.path.join(OUT, f"figC6_累积准确率_{tag}.png"))

    # 顺带导出这张「试次↔结果」对照表，方便逐条核对
    csv = os.path.join(OUT, "..", f"试次分类对照_B{a.subject:02d}.csv")
    with open(csv, "w", encoding="utf-8-sig") as fh:
        fh.write("试次序号,真实标签,预测标签,是否正确,决策值d\n")
        for i in range(len(yte)):
            fh.write(f"{i},{CLS_NAME[yte[i]]},{CLS_NAME[ypred[i]]},"
                     f"{'√' if yte[i]==ypred[i] else '×'},{dec[i]:.6f}\n")
    print(f"  -> {os.path.basename(csv)}")
    print("\n完成。")


if __name__ == "__main__":
    main()
