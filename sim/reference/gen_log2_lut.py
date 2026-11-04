#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成 lognorm.v 用的 log2 查表与逐位参考模型

LUT：log2(1 + k/256)，k = 0..256，Q1.14 / 16 bit（257 项，末项供插值用）
参考模型：与 RTL 同一套算法（前导零 + 查表 + 线性插值 + Q16.14 + ln2 换算）
"""
import os
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "log2_lut.hex")

N_LUT = 257            # 0..256，多一项给插值用
QL = 14                # LUT 小数位 Q1.14


def gen_lut():
    vals = []
    for k in range(N_LUT):
        v = np.log2(1.0 + k / 256.0)
        q = int(round(v * (1 << QL)))
        q = max(0, min((1 << 15) - 1, q))       # Q1.14 非负，范围 [0,2)
        vals.append(q)
    # 末项补 1.0，保证 k=255 时 LUT[256] 可用
    vals[-1] = int(round(1.0 * (1 << QL)))
    with open(OUT, "w", encoding="ascii") as f:
        for v in vals:
            f.write(f"{v:04x}\n")
    print(f"[LUT] {N_LUT} 项 Q1.{QL} → {os.path.basename(OUT)}")
    print(f"      LUT[0]={vals[0]}  LUT[128]={vals[128]}  LUT[256]={vals[256]}")
    return np.array(vals, dtype=np.int64)


LUT = None


def log2_q14(v):
    """与 RTL 逐位等价的 log2，返回 Q16.14"""
    global LUT
    if v <= 0:
        return 0
    e = int(v).bit_length() - 1                 # 等价于 RTL 的 msb_pos
    if e > 63:
        e = 63
    mant = (v << (63 - e)) & ((1 << 64) - 1)
    k = (mant >> 55) & 0xFF
    frac = (mant >> 47) & 0xFF
    l0 = int(LUT[k]); l1 = int(LUT[k + 1])
    interp = l0 + (((l1 - l0) * frac) >> 8)
    return (e << 14) + interp


def lognorm_ref(sz, sz2, T=626):
    """与 lognorm.v 逐位等价的参考实现，返回 Q4.11 特征"""
    n = len(sz)
    num = []
    for i in range(n):
        s, s2 = int(sz[i]), int(sz2[i])
        num.append(max(0, s2 * T - s * s))
    tot = sum(num)
    lg_sum = log2_q14(tot)
    out = []
    for i in range(n):
        lg_i = log2_q14(num[i])
        f = ((lg_i - lg_sum) * 90841 + (1 << 19)) >> 20
        out.append(int(np.clip(f, -32768, 32767)))
    return out


def lognorm_float(sz, sz2, T=626):
    """浮点参考（用于看量化误差量级）"""
    n = len(sz)
    num = np.array([max(0.0, float(s2) * T - float(s) ** 2) for s, s2 in zip(sz, sz2)])
    if num.sum() <= 0:
        return np.zeros(n)
    return np.log(num / num.sum())


if __name__ == "__main__":
    LUT = gen_lut()

    print("\n=== 抽查 log2_q14 ===")
    for v in (1, 2, 256, 1024, 1 << 20, 1 << 40, (1 << 48) - 1):
        got = log2_q14(v) / (1 << 14)
        exp = np.log2(v) if v > 0 else 0
        print(f"  v={v:<18} RTL={got:9.5f}  真值={exp:9.5f}  差={got-exp:+.2e}")

    print("\n=== 端到端对比（从真实 z 序列算 Sz/Sz2，保证物理合法）===")
    rng = np.random.default_rng(11)
    err = []
    for trial in range(6):
        T = 626
        # ★ 必须从真实 z 序列反推 Sz/Sz2，否则随机数不满足 Cauchy-Schwarz
        #   （Sz2 ≥ Sz²/T），会出现 num<0 → 被截到 0 → log(0) = -inf 的假失败
        z = rng.normal(scale=200.0 * (1 + trial), size=(6, T)).astype(np.int64)
        sz = z.sum(axis=1)
        sz2 = (z * z).sum(axis=1)
        a = np.array(lognorm_ref(sz, sz2, T)) / 2048.0
        b = lognorm_float(sz, sz2, T)
        e = np.abs(a - b).max()
        err.append(e)
        print(f"  试次{trial+1}: max|定点-浮点| = {e:.3e}   {'✓' if e < 1e-3 else '✗'}")
    print(f"\n  最差 {max(err):.3e}（规格 < 1e-3）"
          f"  {'✓ 满足' if max(err) < 1e-3 else '✗ 超规格'}")
