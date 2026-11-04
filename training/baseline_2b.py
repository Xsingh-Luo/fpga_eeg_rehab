#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BCI Competition IV 数据集 2b  (MOABB 编号 BNCI2014-004)
CSP + SVM 运动想象二分类基线

════════════════════════════════════════════════════════════════════
数据集事实（已在本地用 MNE 逐个文件核实，勿凭记忆改）
────────────────────────────────────────────────────────────────────
  受试者     : 9 人，健康右利手
  通道       : 3 双极 EEG (C3, Cz, C4) + 3 EOG，共 6 通道
  采样率     : 250 Hz
  类别       : 左手想象 vs 右手想象  —— 二分类
  会话文件   : 每人 **5 个**，命名 B{被试:02d}{会话:02d}{T|E}.gdf
                 B0101T  B0102T  B0103T  B0104E  B0105E
                  └──── 训练集 (T) ────┘  └─ 评价集 (E) ─┘
               与竞赛官方划分一致：训练 = 01T+02T+03T，评价 = 04E+05E
  试次数     : T 会话 120/120/160 → 每人 400；E 会话各 160 → 每人 320
  事件码     : 768 试次开始 | 769 cue 左手 | 770 cue 右手
               783 cue 未知 | 1023 被剔除试次 | 1077-1081 眼动 | 32766 run 开始

  ★ 关键陷阱：**评价集 (04E/05E) 的 GDF 里没有 769/770**，只有 783「cue 未知」
    —— 竞赛当时故意隐藏了标签。真值在 bbci.de 单独公布：
        https://bbci.de/competition/iv/results/ds2b/true_labels.zip
    本仓库已下载到 data/raw/BCICIV_2b_true_labels/，每个 .mat 一个 classlabel 数组，
    长度与 GDF 的 768 事件数**逐会话完全一致**（已验证 20 个会话无一例外），
    所以「第 i 个 768 事件 ↔ classlabel[i]」的对应是可靠的。
    标签取值：1 = 左手，2 = 右手。

════════════════════════════════════════════════════════════════════
两种评估协议（--protocol）—— 报告里两条都要报
────────────────────────────────────────────────────────────────────
  within : 训练集内部 10×10 折交叉验证（与文献可比，偏乐观）
  cross  : 训练集(01T+02T+03T) → 评价集(04E+05E)，用官方真值标签
           ★ 这才是 FPGA 部署时真正面对的场景，数字明显更低，但诚实

════════════════════════════════════════════════════════════════════
时间窗（--tmin / --tmax，相对 768 试次起点，单位秒）
────────────────────────────────────────────────────────────────────
  两类会话的想象期不同：筛查会话 (01T/02T) 为 [2, 6] s，反馈会话为 [3, 7.5] s。
  默认窗 **[3.0, 5.0] s** 落在两者交集内，长度 2 s = 500 点 @250 Hz
  —— 正好等于 FPGA 滑动窗长度，且对两类会话都合法。
  若只想和某篇文献对齐，用 --tmin/--tmax 自行调整。

用法示例
────────────────────────────────────────────────────────────────────
  python baseline_2b.py --protocol within
  python baseline_2b.py --protocol cross --kernel rbf --m 3
  python baseline_2b.py --subjects 1 3 8 --export-params params\\subj01.json
"""
import argparse
import json
import os
import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SESSIONS_T = ["01T", "02T", "03T"]
SESSIONS_E = ["04E", "05E"]

EEG_WANTED = ("C3", "CZ", "C4")
CUE_LEFT, CUE_RIGHT, CUE_UNKNOWN = "769", "770", "783"
TRIAL_START, REJECTED = "768", "1023"


# ─────────────────────────── 数据读取 ───────────────────────────
def _norm_ch(s):
    return s.upper().replace("EEG", "").replace(":", "").replace("-", "").replace(" ", "").strip()


def pick_eeg(raw):
    want = {w: None for w in EEG_WANTED}
    for ch in raw.ch_names:
        n = _norm_ch(ch)
        for w in EEG_WANTED:
            if n == w and want[w] is None:
                want[w] = ch
    missing = [w for w, v in want.items() if v is None]
    if missing:
        raise RuntimeError(f"未找到通道 {missing}；文件通道 = {raw.ch_names}")
    return [want[w] for w in EEG_WANTED]


def load_labels(labels_dir, subj, sess):
    """评价集真值：返回 (n_trials,) 的 0/1 数组；训练集返回 None"""
    if not labels_dir:
        return None
    from scipy.io import loadmat

    p = os.path.join(labels_dir, f"B{subj:02d}{sess}.mat")
    if not os.path.exists(p):
        return None
    y = np.asarray(loadmat(p, squeeze_me=True)["classlabel"]).ravel()
    return np.where(y == 1, 0, 1)          # 1=左手→0, 2=右手→1


def build_session(gdf_path, labels_dir, subj, sess, tmin, tmax, l_freq, h_freq,
                  causal, drop_artifacts):
    """返回 X (n_trials, 3, n_times), y (0/1)"""
    import mne

    raw = mne.io.read_raw_gdf(gdf_path, preload=True, verbose="ERROR")
    raw.pick(pick_eeg(raw))
    raw.filter(l_freq=l_freq, h_freq=h_freq, fir_design="firwin",
               phase="forward" if causal else "zero", verbose="ERROR")

    events, emap = mne.events_from_annotations(raw, verbose="ERROR")

    def code(name):
        hit = [v for k, v in emap.items() if name in str(k)]
        return hit[0] if hit else None

    c_start, c_l, c_r, c_rej = code(TRIAL_START), code(CUE_LEFT), code(CUE_RIGHT), code(REJECTED)
    if c_start is None:
        raise RuntimeError(f"{os.path.basename(gdf_path)}: 找不到 768 试次开始事件")

    starts = events[events[:, 2] == c_start]
    n = len(starts)

    # ── 标签：训练集从 cue 注解取，评价集从真值文件取 ──
    y = None
    if c_l is not None and c_r is not None:
        cue = events[np.isin(events[:, 2], [c_l, c_r])]
        lab_from_ann = np.where(cue[:, 2] == c_l, 0, 1)
        # cue 事件与 768 事件按时间顺序一一对应
        if len(cue) == n:
            y = lab_from_ann
    if y is None:
        y = load_labels(labels_dir, subj, sess)
        if y is None:
            raise RuntimeError(
                f"{os.path.basename(gdf_path)}: 该会话无 cue 标签，且找不到真值文件 "
                f"B{subj:02d}{sess}.mat —— 评价集必须提供 --labels 目录")
    if len(y) != n:
        raise RuntimeError(f"{os.path.basename(gdf_path)}: 标签数 {len(y)} ≠ 试次数 {n}")

    epochs = mne.Epochs(raw, starts, event_id={"t": c_start}, tmin=tmin, tmax=tmax,
                        baseline=None, preload=True, verbose="ERROR")
    X = epochs.get_data()

    # ── 剔除被 1023 标记的试次 ──
    keep = np.ones(n, dtype=bool)
    if drop_artifacts and c_rej is not None:
        sfreq = raw.info["sfreq"]
        rej = raw.annotations.onset[[REJECTED in str(d) for d in raw.annotations.description]]
        if len(rej):
            for i, ev in enumerate(starts):
                a, b = ev[0] / sfreq + tmin, ev[0] / sfreq + tmax
                if np.any((rej >= a) & (rej <= b)):
                    keep[i] = False
    return X[keep], y[keep], int((~keep).sum())


def collect(data_dir, labels_dir, subjects, sessions, a):
    Xs, ys, ns, na = [], [], [], []
    for s in subjects:
        for sess in sessions:
            p = os.path.join(data_dir, f"B{s:02d}{sess}.gdf")
            if not os.path.exists(p):
                continue
            X, y, nrej = build_session(p, labels_dir, s, sess, a.tmin, a.tmax,
                                       a.l_freq, a.h_freq, a.causal, not a.keep_artifacts)
            Xs.append(X); ys.append(y); ns.append(len(y)); na.append(nrej)
    if not Xs:
        raise RuntimeError("没有读到任何数据，检查 --data / --subjects")
    return np.concatenate(Xs), np.concatenate(ys), ns, na


# ─────────────────────────── 分类 ───────────────────────────
def make_pipeline(m, kernel, C):
    from mne.decoding import CSP
    from sklearn.pipeline import Pipeline
    from sklearn.svm import SVC

    return Pipeline([
        ("csp", CSP(n_components=2 * m, reg="ledoit_wolf", log=True, norm_trace=False)),
        ("svm", SVC(kernel=kernel, C=C)),
    ])


def score_cv_within(X, y, m, kernel, C, splits, repeats, seed=42):
    from sklearn.model_selection import RepeatedStratifiedKFold, cross_val_score

    X = X - X.mean(axis=2, keepdims=True)
    cv = RepeatedStratifiedKFold(n_splits=splits, n_repeats=repeats, random_state=seed)
    sc = cross_val_score(make_pipeline(m, kernel, C), X, y, cv=cv, scoring="accuracy")
    return float(sc.mean()), float(sc.std())


def score_cross(Xtr, ytr, Xte, yte, m, kernel, C):
    Xtr = Xtr - Xtr.mean(axis=2, keepdims=True)
    Xte = Xte - Xte.mean(axis=2, keepdims=True)
    pipe = make_pipeline(m, kernel, C)
    pipe.fit(Xtr, ytr)
    acc = float(pipe.score(Xte, yte))
    # kappa（二分类均衡时 kappa = 2*acc - 1）
    return acc, 2 * acc - 1, pipe


def export_params(pipe, path, meta):
    csp = pipe.named_steps["csp"]
    svm = pipe.named_steps["svm"]
    W = np.asarray(csp.filters_).T          # (n_ch, 2m)
    out = dict(meta, csp_W=W.tolist(), svm=dict(
        kernel=svm.kernel,
        support_vectors=svm.support_vectors_.tolist(),
        dual_coef=np.ravel(svm.dual_coef_).tolist(),
        intercept=np.ravel(svm.intercept_).tolist(),
        gamma=(float(svm._gamma) if svm.kernel == "rbf" else None),
        n_support=svm.n_support_.tolist()))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    return path


# 竞赛官方公布的最优结果（kappa），用于对照 —— 出处 bbci.de/competition/iv/results/
OFFICIAL_2B = {
    1: 0.40, 2: 0.21, 3: 0.22, 4: 0.95, 5: 0.86, 6: 0.61, 7: 0.56, 8: 0.85, 9: 0.74,
}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    raw = os.path.join(here, "..", "data", "raw")
    ap = argparse.ArgumentParser(description="BCI IV 2b CSP+SVM 二分类基线")
    ap.add_argument("--data", default=os.path.join(raw, "BCICIV_2b_gdf"))
    ap.add_argument("--labels", default=os.path.join(raw, "BCICIV_2b_true_labels"),
                    help="评价集真值 .mat 目录")
    ap.add_argument("--subjects", type=int, nargs="*", default=list(range(1, 10)))
    ap.add_argument("--protocol", choices=["within", "cross"], default="within")
    ap.add_argument("--tmin", type=float, default=3.5,
                    help="相对 768 试次起点（秒）。默认 3.5 由 sweep_2b.py 扫出")
    ap.add_argument("--tmax", type=float, default=6.0)
    ap.add_argument("--l-freq", type=float, default=8.0)
    ap.add_argument("--h-freq", type=float, default=30.0)
    ap.add_argument("--causal", action="store_true", help="因果滤波（模拟在线）")
    ap.add_argument("--m", type=int, default=2, help="每侧 CSP 滤波器对数 → 特征 2m 维")
    ap.add_argument("--kernel", choices=["linear", "rbf"], default="linear")
    ap.add_argument("--C", type=float, default=1.0)
    ap.add_argument("--splits", type=int, default=10)
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--keep-artifacts", action="store_true")
    ap.add_argument("--export-params", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    import mne
    mne.set_log_level("ERROR")

    print("=" * 84)
    print(f"BCI IV 2b | 协议={a.protocol} | 窗=[{a.tmin},{a.tmax}]s(相对768) "
          f"| 带通={a.l_freq}-{a.h_freq}Hz | 因果={a.causal} | 核={a.kernel} C={a.C} | m={a.m}")
    print("=" * 84)

    if a.protocol == "within":
        hdr = f"{'被试':<6}{'试次':<8}{'准确率':<12}{'kappa':<10}{'官方最优kappa':<14}"
        print(hdr); print("-" * 84)
        accs = []
        for s in a.subjects:
            try:
                X, y, ns, na = collect(a.data, a.labels, [s], SESSIONS_T, a)
                acc, sd = score_cv_within(X, y, a.m, a.kernel, a.C, a.splits, a.repeats)
                k = 2 * acc - 1
                accs.append(acc)
                print(f"B{s:02d}   {len(y):<8d}{acc*100:6.2f}%±{sd*100:<5.1f}{k:6.3f}   "
                      f"{OFFICIAL_2B.get(s, float('nan')):.2f}")
            except Exception as e:
                print(f"B{s:02d}   失败: {type(e).__name__}: {e}")
        if accs:
            print("-" * 84)
            print(f"平均准确率 {np.mean(accs)*100:.2f}%   平均 kappa {2*np.mean(accs)-1:.3f}   "
                  f"(官方最优平均 kappa 0.600)")
    else:
        hdr = f"{'被试':<6}{'训练T':<8}{'评价E':<8}{'准确率':<12}{'kappa':<10}{'官方最优kappa':<14}"
        print(hdr); print("-" * 84)
        accs = []
        for s in a.subjects:
            try:
                Xtr, ytr, _, _ = collect(a.data, a.labels, [s], SESSIONS_T, a)
                Xte, yte, _, _ = collect(a.data, a.labels, [s], SESSIONS_E, a)
                acc, k, pipe = score_cross(Xtr, ytr, Xte, yte, a.m, a.kernel, a.C)
                accs.append(acc)
                note = ""
                if a.export_params and s == a.subjects[0]:
                    meta = dict(dataset="BCI_IV_2b", subject=f"B{s:02d}", fs=250,
                                n_ch=Xtr.shape[1], win_samples=Xtr.shape[2],
                                band=[a.l_freq, a.h_freq], m=a.m, kernel=a.kernel,
                                tmin=a.tmin, tmax=a.tmax, causal=bool(a.causal),
                                protocol="cross")
                    note = "  -> " + os.path.relpath(export_params(pipe, a.export_params, meta))
                print(f"B{s:02d}   {len(ytr):<8d}{len(yte):<8d}{acc*100:6.2f}%     {k:6.3f}   "
                      f"{OFFICIAL_2B.get(s, float('nan')):.2f}{note}")
            except Exception as e:
                print(f"B{s:02d}   失败: {type(e).__name__}: {e}")
        if accs:
            print("-" * 84)
            print(f"平均准确率 {np.mean(accs)*100:.2f}%   平均 kappa {2*np.mean(accs)-1:.3f}   "
                  f"(官方最优平均 kappa 0.600)")

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(dict(config=vars(a)), f, indent=2, ensure_ascii=False, default=str)
        print(f"配置已写入 {a.out}")


if __name__ == "__main__":
    main()
