#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
设计 FBCSP 滤波器组并导出给 MATLAB 用（避免 MATLAB 端依赖 Signal Processing Toolbox）

FBCSP 原始配置（Ang et al. 2012 / BCI IV 2b 冠军方案）：
  9 个子带，4–40 Hz，每带 4 Hz 宽：
    4-8, 8-12, 12-16, 16-20, 20-24, 24-28, 28-32, 32-36, 36-40

  另给一个聚焦 MI 频段的精简配置（6 带，8–32 Hz），用于对比：
    8-12, 12-16, 16-20, 20-24, 24-28, 28-32

滤波器：4 阶巴特沃斯带通 = 2 个二阶段（biquad）。
  说明：FBCSP 相邻子带**故意重叠**，不需要陡峭截止，所以低阶就够——
  这点对 FPGA 资源很关键（9 带 × 2 节 = 18 个 biquad/通道，
  而不是如果追求陡峭截止所需的 9 带 × 8 节 = 72 个）。

输出：matlab/data/fbcsp_filters.mat
  含 sos9 (9×2×6)、sos6 (6×2×6)、bands9、bands6
"""
import os
import sys

import numpy as np
from scipy import signal
from scipy.io import savemat

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FS = 250.0
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "matlab", "data")

BANDS9 = [(4, 8), (8, 12), (12, 16), (16, 20), (20, 24),
          (24, 28), (28, 32), (32, 36), (36, 40)]
BANDS6 = [(8, 12), (12, 16), (16, 20), (20, 24), (24, 28), (28, 32)]
BANDS4 = [(8, 14), (14, 20), (20, 26), (26, 32)]
# ★ 最终配置：3 个宽带子带（实测 79.97%，优于 4/6/9 带）
BANDS3 = [(8, 16), (16, 24), (24, 32)]

ORDER = 2          # 原型阶数；带通后 = 2*ORDER = 4 阶 = 2 个 biquad


def design(bands):
    sos_all = []
    for lo, hi in bands:
        sos = signal.butter(ORDER, [lo, hi], btype="band", fs=FS, output="sos")
        sos_all.append(sos)
    return np.array(sos_all)          # (n_bands, n_sections, 6)


def report(name, bands, sos_all):
    print(f"\n=== {name}：{len(bands)} 个子带 ===")
    print(f"{'子带':>10} {'节数':>5} {'带内起伏dB':>11} {'邻带抑制dB':>11} {'@带中心群延迟ms':>16}")
    for (lo, hi), sos in zip(bands, sos_all):
        w, h = signal.sosfreqz(sos, worN=16384, fs=FS)
        mag = np.abs(h)
        pb = (w >= lo) & (w <= hi)
        ref = mag[pb].max()
        ripple = 20 * np.log10(mag[pb].max() / mag[pb].min())
        adj = lo - 2 if lo > 4 else hi + 2
        att = 20 * np.log10(mag[np.argmin(np.abs(w - adj))] / ref + 1e-12)
        # ★ 群延迟 = -dφ/dω，rad/Hz 必须除以 2π 才是秒
        gd = -np.gradient(np.unwrap(np.angle(h)), w) / (2 * np.pi)
        c = (lo + hi) / 2
        gdc = gd[np.argmin(np.abs(w - c))] * 1000
        print(f"{lo:>4}-{hi:<4} {sos.shape[0]:>5} {ripple:>11.2f} {att:>11.1f} {gdc:>16.1f}")

    # 各子带中心群延迟差异（差异大会导致子带间时间失配）
    gds = []
    for (lo, hi), sos in zip(bands, sos_all):
        w, h = signal.sosfreqz(sos, worN=16384, fs=FS)
        gd = -np.gradient(np.unwrap(np.angle(h)), w) / (2 * np.pi)
        c = (lo + hi) / 2
        gds.append(gd[np.argmin(np.abs(w - c))] * 1000)
    print(f"  各子带中心群延迟: {min(gds):.1f} ~ {max(gds):.1f} ms "
          f"(差异 {max(gds)-min(gds):.1f} ms)")


def main():
    os.makedirs(OUT, exist_ok=True)
    sos3 = design(BANDS3)
    sos6 = design(BANDS6)
    report("最终配置 · 3 宽带子带", BANDS3, sos3)
    report("对比配置 · 6 子带", BANDS6, sos6)

    savemat(os.path.join(OUT, "fbcsp_filters.mat"), {
        "sos3": sos3, "sos6": sos6,
        "bands3": np.array(BANDS3, dtype=float),
        "bands6": np.array(BANDS6, dtype=float),
        "fs": FS, "order": ORDER,
    }, do_compression=True)

    print(f"\n已导出 → {os.path.abspath(os.path.join(OUT, 'fbcsp_filters.mat'))}")
    print(f"\nFPGA 资源提示（最终配置 3 带）：")
    print(f"  3 带 × 2 节 = 6 个 biquad / 通道")
    print(f"  8 通道 → 48 个 biquad，每采样点 48×5 = 240 MAC")
    print(f"  250 Hz → 60 kMAC/s；100 MHz 下每采样周期(400k 周期)仅需 240 周期")


if __name__ == "__main__":
    main()
