#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""eeg_quality_check.py —— OpenBCI 记录质量验收（采完先跑这个）

用法:
  python sim/host_tools/eeg_quality_check.py --bin data/raw/openbci/<日期>/xxx_raw.bin [--events ..._events.csv]
  python sim/host_tools/eeg_quality_check.py --csv data/raw/openbci/<日期>/xxx_raw.csv
  python sim/host_tools/eeg_quality_check.py --gui-txt "C:\Users\...\OpenBCI_GUI\Recordings\OpenBCI-RAW-....txt"

说明:
  · OpenBCI 原始 EEG 本来就有几十 mV 的直流偏置（官方样例也一样），DC 不是判据；
    训练前会做 0.5 Hz 高通 + 50/100/150 Hz notch，所以同时给出"滤波后"指标。
  · 判据（滤波后）:
      8-30 Hz 带内 RMS : PASS 2-40 uV | WARN 40-80 | FAIL >80 或 <0.5
      50 Hz 突出度      : PASS <50x  | WARN 50-500 | FAIL >500（官样例 ≈1x）
      贴轨             : FAIL 若 |x|>=180 mV
      通道间相关        : >0.95 视为共模主导（FAIL）
"""
import argparse, os, sys, csv
import numpy as np

FS = 250.0
UV = 0.022351744455307063
NAMES = ["Fp1", "Fp2", "C3", "C4", "P7", "P8", "O1", "O2"]


def parse_bin(path):
    raw = np.fromfile(path, dtype=np.uint8)
    idx, pos = [], 0
    while pos + 33 <= len(raw):
        if raw[pos] in (0x41, 0xA0) and 0xC0 <= raw[pos + 32] <= 0xCF:
            idx.append(pos); pos += 33
        else:
            pos += 1
    pkt = raw[np.array(idx)[:, None] + np.arange(33)]
    cc = np.zeros((len(idx), 8), dtype=np.int64)
    for c in range(8):
        b = pkt[:, 2 + c * 3:2 + c * 3 + 3].astype(np.int64)
        v = (b[:, 0] << 16) | (b[:, 1] << 8) | b[:, 2]
        cc[:, c] = np.where(v >= (1 << 23), v - (1 << 24), v)
    return cc * UV, len(raw) - 33 * len(idx)


def parse_csv(path):
    import pandas as pd
    df = pd.read_csv(path)
    cols = [f"ch{i}_count" for i in range(1, 9)]
    return df[cols].to_numpy(dtype=np.int64) * UV, 0


def parse_gui_txt(path):
    """OpenBCI GUI 自己保存的 RAW txt（单位 uV）：第一列 sample index，接着 8 通道。"""
    rows = []
    for line in open(path, "r", errors="replace").read().splitlines():
        line = line.strip()
        if not line or line.startswith("%"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9:
            continue
        try:
            rows.append([float(x) for x in parts[:9]])
        except ValueError:
            continue
    a = np.array(rows, dtype=float)
    return a[:, 1:9], 0


def notch_hp(X):
    from scipy import signal
    Y = X.astype(float).copy()
    for f0 in (50.0, 100.0):
        b, a = signal.iirnotch(f0 / (FS / 2), 30)
        Y = signal.filtfilt(b, a, Y, axis=0)
    b, a = signal.butter(2, 0.5 / (FS / 2), btype="high")
    return signal.filtfilt(b, a, Y, axis=0)


def band_rms(x, f1, f2, order=6):
    from scipy import signal
    b, a = signal.butter(order, [f1 / (FS / 2), f2 / (FS / 2)], btype="band")
    y = signal.lfilter(b, a, x, axis=0)
    return np.sqrt(np.mean(y ** 2, axis=0))


def p50_ratio(x):
    from scipy import signal
    f, P = signal.welch(x, fs=FS, nperseg=2048, axis=0)
    i50 = np.argmin(np.abs(f - 50))
    near = (f > 40) & (f < 60) & (np.abs(f - 50) > 2)
    return P[i50] / (np.median(P[near], axis=0) + 1e-12)


def grade_rms(v):
    if 2 <= v <= 40: return "PASS"
    if 0.5 <= v <= 80: return "WARN"
    return "FAIL"


def grade_50(v):
    if v < 50: return "PASS"
    if v < 500: return "WARN"
    return "FAIL"


def table(X, tag):
    rms = band_rms(X, 8, 30)
    mx = np.abs(X).max(axis=0) / 1000.0
    dc = np.abs(X.mean(axis=0)) / 1000.0
    r50 = p50_ratio(X)
    from scipy import signal
    b, a = signal.butter(6, [8 / (FS / 2), 30 / (FS / 2)], btype="band")
    Yb = signal.lfilter(b, a, X - X.mean(0), axis=0)
    C = np.corrcoef(Yb.T)
    corr = (C.sum() - 8) / 56
    print("\n[%s]   ch | 8-30RMS(uV) | max|x|(mV) | |DC|(mV) | 50Hz/邻近 | 判定" % tag)
    verdicts = []
    for c in range(8):
        g_rms = grade_rms(rms[c])
        g_rail = "FAIL" if mx[c] >= 180 else "PASS"
        g_50 = grade_50(r50[c])
        v = "FAIL" if "FAIL" in (g_rms, g_50, g_rail) else ("WARN" if "WARN" in (g_rms, g_50) else "PASS")
        verdicts.append(v)
        print("        %-4s| %10.1f | %9.1f  | %8.1f  | %9.0fx | %s" % (
            NAMES[c], rms[c], mx[c], dc[c], r50[c], v))
    print("        通道判定:", " ".join("%s=%s" % (NAMES[c], verdicts[c]) for c in range(8)))
    print("        8-30Hz 通道相关均值: %.2f   (正常 0.2-0.8；>0.95=共模主导)" % corr)
    return verdicts, corr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin"); ap.add_argument("--csv"); ap.add_argument("--gui-txt")
    ap.add_argument("--events")
    a = ap.parse_args()
    if a.bin:
        X, dropped = parse_bin(a.bin); src = a.bin
    elif a.csv:
        X, dropped = parse_csv(a.csv); src = a.csv
    elif a.gui_txt:
        X, dropped = parse_gui_txt(a.gui_txt); src = a.gui_txt
    else:
        print("需要 --bin / --csv / --gui-txt"); return 2
    print("=" * 70)
    print("EEG 质量验收:", src)
    print("  samples=%d  (%.1f s @250Hz)  丢字节=%d" % (X.shape[0], X.shape[0] / FS, dropped))
    v_raw, corr_raw = table(X, "原始（含 DC/工频，仅供诊断）")
    v_f, corr_f = table(notch_hp(X), "50/100/150Hz notch + 0.5Hz 高通后（训练实际看到的）")

    if a.events and os.path.isfile(a.events):
        rows = list(csv.DictReader(open(a.events, encoding="utf-8-sig")))
        cues = [r for r in rows if r.get("event") == "cue"]
        labs = [r.get("label") for r in cues]
        print("\n  事件表: trials(cue)=%d  左手=%d  右手=%d" % (
            len(cues), labs.count("0"), labs.count("1")))
        print("  事件/标签:", "PASS" if (len(cues) >= 40 and abs(labs.count("0") - labs.count("1")) <= 3) else "FAIL")

    nfail = v_f.count("FAIL")
    print("\n" + "=" * 70)
    print(" 说明: DC 偏置不判失败（OpenBCI 官方样例也有几十 mV）；看上面『滤波后』那组。")
    if corr_f > 0.95:
        print(" 总体: FAIL —— 通道相关 %.2f ≈ 1，共模噪声主导：查参考/地（SRB、BIAS 耳夹）与电极接触。" % corr_f)
    elif nfail >= 5:
        print(" 总体: FAIL —— 多数通道滤波后仍不合格（8-30Hz RMS 过大 / 50Hz 突出）。")
    elif nfail >= 1:
        print(" 总体: WARN —— %d 个通道不合格，建议改善接触后再采。" % nfail)
    else:
        print(" 总体: PASS —— 可以正式采集（建议再用 OpenBCI GUI Impedance Check 看阻抗 <10kOhm）。")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
