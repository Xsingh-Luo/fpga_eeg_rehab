#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
静默错误自检 —— 三类"不报错但算错了"的最小检测

适用：任何涉及 矩阵运算 / 量纲换算 / 参数折叠 的代码。
      这些错误不会抛异常，结果也"看着合理"，只能靠主动构造小例子撞出来。

用法：
    python numeric_selfcheck.py           # 跑全部自检
    python numeric_selfcheck.py --only T  # 只跑矩阵转置检查

退出码：0 = 全部通过；1 = 有检查未通过。
"""
import argparse
import sys

import numpy as np


# ══════════════════════════════════════════════════════════════════
# 检查 1：矩阵投影方向
# ══════════════════════════════════════════════════════════════════
def check_transpose():
    """
    判别：空间滤波矩阵 W 的形状是 (n_ch, n_comp)，**列**才是滤波器。
          正确投影是 z = Wᵀx。

    陷阱：若 W 恰好是方阵（如 3 通道 m=2 时 3×3），
          写成 W·x 也不会报错，结果同样"看着合理"。
    """
    W = np.array([[1.0, 2.0], [3.0, 4.0]])      # (n_ch=2, n_comp=2)
    x = np.array([10.0, 100.0])

    ref = W.T @ x                                # 期望 [310, 420]
    cases = {
        "Wᵀx  (正确, 'cj,nct->njt')": np.einsum("cj,nct->njt", W, x[None, :, None]).ravel(),
        "W·x  (错误, 'cd,ndt->nct')": np.einsum("cd,ndt->nct", W, x[None, :, None]).ravel(),
    }

    print("\n[1] 矩阵投影方向")
    print(f"    W = {W.tolist()}, x = {x.tolist()}")
    print(f"    期望 Wᵀx = {ref}")
    ok = True
    for name, got in cases.items():
        good = np.allclose(got, ref)
        mark = "✓" if good else "✗"
        print(f"    {mark} {name:<32} → {got}")
        if "错误" in name:
            if good:
                print("      ⚠ 这个'错误写法'竟然也对？检查你的 W 是否是特殊矩阵")
            ok &= True          # 错误写法本就应该不等于 ref
        else:
            ok &= good

    # 方阵陷阱演示
    Wsq = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 10.0]])
    xs = np.array([1.0, 2.0, 3.0])
    a, b = Wsq.T @ xs, Wsq @ xs
    print(f"    方阵陷阱: 3×3 时 Wᵀx={a}, W·x={b} —— 形状都合法，不报错，但结果不同")
    return ok


# ══════════════════════════════════════════════════════════════════
# 检查 2：量纲换算（群延迟 rad/Hz → 秒）
# ══════════════════════════════════════════════════════════════════
def check_2pi():
    """
    判别：从频率响应求群延迟时，-dφ/df 的单位是 rad/Hz，换算成秒要除以 2π。

    交叉验证方法：用一个解析值已知的滤波器。
          FIR 对称抽头的群延迟必然是 (N-1)/2 个采样点。
    """
    from scipy import signal  # 可选依赖

    fs, n_tap = 250.0, 128
    taps = signal.firwin(n_tap, [8, 30], pass_zero=False, fs=fs, window="hamming")
    w, h = signal.freqz(taps, worN=8192, fs=fs)

    dphi = -np.gradient(np.unwrap(np.angle(h)), w)      # rad/Hz
    gd_wrong = dphi * 1000                               # 直接当 ms —— 错
    gd_right = dphi / (2 * np.pi) * 1000                 # 除以 2π —— 对

    analytic = (n_tap - 1) / 2 / fs * 1000               # 254 ms
    print("\n[2] 量纲换算（群延迟 rad/Hz → 秒）")
    print(f"    解析值 (N-1)/2/fs = {analytic:.1f} ms")
    print(f"    ✗ 漏 2π：{gd_wrong[np.argmin(abs(w-12))]:.1f} ms  (比值 {gd_wrong[np.argmin(abs(w-12))]/analytic:.2f} ≈ 2π)")
    print(f"    ✓ 除 2π：{gd_right[np.argmin(abs(w-12))]:.1f} ms")
    return abs(gd_right[np.argmin(abs(w - 12))] - analytic) < 2.0


# ══════════════════════════════════════════════════════════════════
# 检查 3：折叠层次
# ══════════════════════════════════════════════════════════════════
def check_fold_layer():
    """
    判别：把算子 A 折叠进下游参数后，输入必须还给**未经 A 处理**的原始数据。

    典型错误：
        折叠后 W_eff = MᵀW，却继续把已经乘过 M 的数据喂给 W_eff
        → 等于把 M 施加两次 → 性能无声下降，代码不报错
    """
    rng = np.random.default_rng(0)
    n_ch, n_comp, T = 3, 2, 200

    M = np.array([[1.2, 0.1, 0.0], [0.0, 1.1, 0.2], [0.1, 0.0, 0.9]])
    W = rng.normal(size=(n_ch, n_comp))
    x = rng.normal(size=(n_ch, T))

    ref = W.T @ (M @ x)                 # 正确：先对齐再投影
    folded = (M.T @ W).T @ x            # 折叠：W_eff = MᵀW，喂**未对齐**数据
    double = (M.T @ W).T @ (M @ x)      # 错误：折叠后还喂已对齐数据

    e_fold = np.abs(folded - ref).max()
    e_dbl = np.abs(double - ref).max()

    print("\n[3] 折叠层次（参数折进下游后，输入要还给原始数据）")
    print(f"    ✓ 折叠正确  最大差 = {e_fold:.3e}")
    print(f"    ✗ 双重施加  最大差 = {e_dbl:.3e}  ← 不报错，但结果全错")
    return e_fold < 1e-10 and e_dbl > 1e-3


# ══════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["T", "2", "F"], help="只跑某一项")
    args = ap.parse_args()

    print("=" * 62)
    print(" 静默错误自检 —— 三类「不报错但算错了」的最小检测")
    print("=" * 62)

    results = {}
    if args.only in (None, "T"):
        results["矩阵投影方向"] = check_transpose()
    if args.only in (None, "2"):
        try:
            results["量纲换算"] = check_2pi()
        except ImportError:
            print("\n[2] 跳过（需要 scipy）")
    if args.only in (None, "F"):
        results["折叠层次"] = check_fold_layer()

    print("\n" + "=" * 62)
    all_ok = all(results.values())
    for k, v in results.items():
        print(f"  {'✓' if v else '✗'} {k}")
    print("=" * 62)
    print("\n提示：把这三项加进你自己的项目，每次改动核心数值代码都跑一遍。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
