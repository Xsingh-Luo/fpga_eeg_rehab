#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定点位宽余量检查器 —— 在综合之前把"量化会溢出"这件事抓出来

适用：任何 浮点参数 → FPGA 定点系数 的场景（滤波器系数、矩阵权重、偏置等）。

用法：
    python check_qmargin.py params.json
    python check_qmargin.py --demo

params.json 格式：
    {
      "targets": [
        {"name": "IIR SOS",      "values": [...],  "bits": 18, "frac": 14},
        {"name": "CSP W",        "values": [...],  "bits": 16, "frac": 14},
        {"name": "SVM b",        "values": [...],  "bits": 32, "frac": 23}
      ]
    }

    bits = 总位宽（含符号位），frac = 小数位
    → 可表示范围 [-(2^(bits-1))/2^frac, (2^(bits-1)-1)/2^frac]

输出：每个目标的 最大绝对值 / 上限 / 余量百分比 / 饱和个数。
退出码：0 = 全部通过；1 = 存在溢出。
"""
import argparse
import json
import sys


def check(name, values, bits, frac):
    lo = -(2 ** (bits - 1)) / (2 ** frac)
    hi = (2 ** (bits - 1) - 1) / (2 ** frac)
    vmax = max(abs(v) for v in values) if values else 0.0
    n_sat = sum(1 for v in values if v > hi or v < lo)
    margin = (1 - vmax / hi) * 100 if hi > 0 else 0.0
    return {
        "name": name, "bits": bits, "frac": frac,
        "range": (lo, hi), "vmax": vmax, "n": len(values),
        "n_sat": n_sat, "margin_pct": margin,
    }


def report(r, quiet_ok=False):
    ok = r["n_sat"] == 0
    tag = "[ OK ]" if ok else "[溢出]"
    if ok and quiet_ok and r["margin_pct"] > 30:
        return ok
    print(f"  {tag} {r['name']:<22} "
          f"Q{r['bits']-1-r['frac']}.{r['frac']}/{r['bits']}bit  "
          f"max={r['vmax']:.4f}  范围=[{r['range'][0]:.3f}, {r['range'][1]:.3f}]  "
          f"余量={r['margin_pct']:.1f}%"
          + (f"  ★ 饱和 {r['n_sat']}/{r['n']} 个" if not ok else ""))
    return ok


def demo():
    """用一组真实案例说明什么情况下会翻车"""
    # 真实的切比雪夫 II 型带通，第 4/5 节的 b1 会逼近 −1.99。
    # Q2.14/16bit 能表示的上限是 32767/16384 = 1.99994 —— 只剩约 0.5% 余量。
    sos = [
        0.95, -1.99,  0.95, -1.9764,  0.4121,
        0.32, -1.99,  0.32, -1.8403,  0.2944,
        0.28, -1.95,  0.28, -1.7600,  0.2500,
    ]
    cases = [
        ("IIR 系数 Q2.14/16bit", sos, 16, 14),
        ("IIR 系数 Q3.14/18bit", sos, 18, 14),
        # 折叠类变换会把权重动态范围推高，归一化后能省回 2 bit
        ("CSP W（折叠后未归一化）", [0.80, -1.20, 3.81, 2.90], 16, 14),
        ("CSP W（归一化到 0.875）", [0.184, -0.276, 0.875, 0.666], 16, 14),
    ]
    print("演示：位宽余量检查\n")
    for name, vals, bits, frac in cases:
        report(check(name, vals, bits, frac))
    print("\n结论：")
    print("  · 16bit Q2.14 的上限是 1.99994，装 |b1|=1.99 只剩约 0.5% 余量")
    print("    → 一旦系数再大一点就饱和，建议直接上 18bit Q3.14")
    print("  · 折叠类变换会把权重动态范围推高（本例 3.81）→ 16bit 装不下")
    print("    → 若下游只用到比值/归一化，先把参数归一化到接近满量程再量化，可省回 2 bit")
    print("\n（演示里故意包含了会溢出的用例，所以这里不返回错误码）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="定点位宽余量检查器")
    ap.add_argument("param_file", nargs="?", help="参数 JSON 文件")
    ap.add_argument("--demo", action="store_true", help="运行内置演示")
    ap.add_argument("--quiet", action="store_true", help="只打印有风险的目标")
    args = ap.parse_args()

    if args.demo:
        return demo()
    if not args.param_file:
        ap.print_help()
        return 2

    with open(args.param_file, encoding="utf-8") as f:
        spec = json.load(f)

    print(f"检查 {args.param_file}\n")
    all_ok = True
    for t in spec["targets"]:
        vals = t["values"]
        if isinstance(vals, list) and vals and isinstance(vals[0], list):
            vals = [x for row in vals for x in row]      # 二维压平
        r = check(t["name"], vals, t["bits"], t["frac"])
        all_ok &= report(r, quiet_ok=args.quiet)

    print()
    if all_ok:
        print("✓ 全部通过")
        return 0
    print("✗ 存在饱和 —— 上板前必须处理：")
    print("  1) 提高整数位（增加总位宽）")
    print("  2) 或利用算法不变性做归一化（若该参数在下游只以比值形式出现）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
