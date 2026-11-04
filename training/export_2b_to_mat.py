#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把 BCI Competition IV 2b 导出成 MATLAB 可直接 load 的 .mat

要点：
  · 导出**整数计数域**（int16），与 FPGA 的定点输入保持一致，而不是 µV 浮点
  · 导出**未滤波**的较宽时间窗 [2.5, 7.0] s，让 MATLAB 端自己做去DC+带通，
    再裁剪到 [3.5, 6.0] s —— 这样 MATLAB 验证的是完整算法链，不是半个
  · 标签来源按会话类型分叉（T 从 GDF 注解，E 从官方真值），见资源清单 §2.1

输出: matlab/data/subj01.mat ... subj09.mat
     每个含 Xtr (Ntr,3,1125) int16 / ytr (Ntr,1) uint8 / Xte / yte / meta
"""
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
from scipy.io import savemat  # noqa: E402

mne.set_log_level("ERROR")

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "..", "data", "raw")
GDF = os.path.join(RAW, "BCICIV_2b_gdf")
LAB = os.path.join(RAW, "BCICIV_2b_true_labels")
OUT = os.path.join(HERE, "..", "matlab", "data")

FS = 250.0
T_EXPORT = (2.5, 7.0)          # 导出窗（相对事件 768），4.5 s = 1125 点
T_USE = (3.5, 6.0)             # MATLAB 端裁剪窗，2.5 s = 626 点
SESS_T = ["01T", "02T", "03T"]
SESS_E = ["04E", "05E"]


def pick_eeg(raw):
    want = {"C3": None, "CZ": None, "C4": None}
    for ch in raw.ch_names:
        n = ch.upper().replace("EEG", "").replace(":", "").strip()
        if n in want and want[n] is None:
            want[n] = ch
    return [want["C3"], want["CZ"], want["C4"]]


def one_session(subj, sess):
    """返回 (n,3,1125) float µV, y (n,), keep_mask"""
    path = os.path.join(GDF, f"B{subj:02d}{sess}.gdf")
    raw = mne.io.read_raw_gdf(path, preload=True, verbose="ERROR")
    raw.pick(pick_eeg(raw))

    events, emap = mne.events_from_annotations(raw, verbose="ERROR")

    def code(name):
        h = [v for k, v in emap.items() if name in str(k)]
        return h[0] if h else None

    c_start, c_l, c_r, c_rej = code("768"), code("769"), code("770"), code("1023")
    starts = events[events[:, 2] == c_start]
    n = len(starts)

    # ── 标签 ──
    y = None
    if c_l is not None and c_r is not None:
        cue = events[np.isin(events[:, 2], [c_l, c_r])]
        if len(cue) == n:
            y = np.where(cue[:, 2] == c_l, 0, 1).astype(np.uint8)
    if y is None:
        from scipy.io import loadmat
        y = np.asarray(loadmat(os.path.join(LAB, f"B{subj:02d}{sess}.mat"),
                               squeeze_me=True)["classlabel"]).ravel()
        y = np.where(y == 1, 0, 1).astype(np.uint8)
    assert len(y) == n, f"{sess}: 标签 {len(y)} != 试次 {n}"

    ep = mne.Epochs(raw, starts, event_id={"t": c_start},
                    tmin=T_EXPORT[0], tmax=T_EXPORT[1],
                    baseline=None, preload=True, verbose="ERROR")
    X = ep.get_data()                                   # (n, 3, 1125) 单位 V

    # ── 伪迹剔除（1023 注解落在导出窗内即丢弃）──
    keep = np.ones(n, dtype=bool)
    if c_rej is not None:
        rej = raw.annotations.onset[[("1023" in str(d)) for d in raw.annotations.description]]
        for i, ev in enumerate(starts):
            a, b = ev[0] / FS + T_EXPORT[0], ev[0] / FS + T_EXPORT[1]
            if np.any((rej >= a) & (rej <= b)):
                keep[i] = False
    return X * 1e6, y, keep                                # V → µV


def main():
    os.makedirs(OUT, exist_ok=True)

    # 先扫一遍全局动态范围，确定统一的计数换算增益 G
    print("扫描动态范围以确定定标增益 G ...")
    pk = 0.0
    for s in range(1, 10):
        for sess in SESS_T + SESS_E:
            X, _, keep = one_session(s, sess)
            pk = max(pk, float(np.abs(X[keep]).max()))
    # 让满量程落在 int16 的 90%，留 10% 余量给滤波过冲
    G = float(np.floor(32767 * 0.90 / pk * 100) / 100)
    print(f"  全局峰值 = {pk:.1f} µV  →  G = {G} counts/µV  (1 count = {1/G:.4f} µV)")

    for s in range(1, 10):
        Xtr, ytr, Xte, yte = [], [], [], []
        for sess in SESS_T:
            X, y, keep = one_session(s, sess)
            Xtr.append(X[keep]); ytr.append(y[keep])
        for sess in SESS_E:
            X, y, keep = one_session(s, sess)
            Xte.append(X[keep]); yte.append(y[keep])

        Xtr = np.concatenate(Xtr); ytr = np.concatenate(ytr)
        Xte = np.concatenate(Xte); yte = np.concatenate(yte)

        # µV → int16 计数（与 FPGA 定点输入一致），饱和保护
        Xtr_i = np.clip(np.round(Xtr * G), -32768, 32767).astype(np.int16)
        Xte_i = np.clip(np.round(Xte * G), -32768, 32767).astype(np.int16)

        meta = dict(fs=FS, G=G, n_ch=3, win_export=T_EXPORT, win_use=T_USE,
                    ch_names=["C3", "Cz", "C4"], subject=s,
                    sessions_train=SESS_T, sessions_eval=SESS_E,
                    note="int16 counts; MATLAB reshapes to (n_ch, n_times) on load")
        savemat(os.path.join(OUT, f"subj{s:02d}.mat"),
                {"Xtr": Xtr_i, "ytr": ytr.reshape(-1, 1),
                 "Xte": Xte_i, "yte": yte.reshape(-1, 1), "meta": meta},
                do_compression=True)
        mb = os.path.getsize(os.path.join(OUT, f"subj{s:02d}.mat")) / 1e6
        print(f"  subj{s:02d}: 训练 {len(ytr):4d} 试次 / 评价 {len(yte):4d} 试次  "
              f"({Xtr_i.shape[2]} 点 @ {FS:.0f} Hz)  {mb:.1f} MB")
    print(f"\n完成，输出目录: {os.path.abspath(OUT)}")


if __name__ == "__main__":
    main()
