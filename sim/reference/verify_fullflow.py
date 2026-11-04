#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
全流程仿真编排 + 逐位参考流水线

① 生成激励与参数（真实训练结果 params/csp_svm_params.json）
② 调 iverilog 跑 tb_fullflow.v（含 AXI 主设备 BFM 模拟 PS）
③ 用**逐位参考流水线**复算整条链，与 RTL 的决策逐拍比对

参考流水线覆盖：
    dc_block → biquad(3带×2节) → csp_project(3带) → win_var
    → lognorm → MIBUF 选 4 维 → svm_core
每一步都与 RTL 用同一套定点规则，因此期望**逐位一致**。

用法：python verify_fullflow.py
"""
import json
import os
import subprocess
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RTL = os.path.join(ROOT, "src", "pl", "rtl")
sys.path.insert(0, HERE)

from gen_log2_lut import gen_lut, lognorm_ref  # noqa: E402
import gen_log2_lut as G  # noqa: E402

N_CH, N_BAND, N_COMP, FEAT_DIM, WIN_LEN = 3, 3, 2, 4, 626
SEL = [1, 2, 4, 3]


def sat(v, bits):
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return int(max(lo, min(hi, v)))


# ── M3 去直流（与 dc_block.v 逐位一致）──
#   ★ 关键：RTL 里**反馈用的是未截断的 y_ext（48 位）**，
#     只有输出口才饱和到 24 位。参考模型若把截断后的值反馈回去，
#     会因为反复截断累积出直流漂移，几十个样本后就明显偏离。
def dc_run(x, a=32361):
    xp, yacc, out = 0, 0, []
    for v in x:
        inner = yacc + v - xp
        y_ext = (a * inner) >> 15          # ★ RTL 是 >>> 15（算术右移）
        xp = v
        yacc = y_ext                        # ★ 反馈未截断值
        out.append(sat(y_ext, 24))          # ★ 只有输出饱和
    return out


# ── M4 biquad（转置直接 II 型，Q3.14）──
def biquad(x, c):
    b0, b1, b2, a1, a2 = c
    s1 = s2 = 0
    out = []
    for v in x:
        y = sat((b0 * v + s1) >> 14, 24)
        s1 = b1 * v - a1 * y + s2
        s2 = b2 * v - a2 * y
        out.append(y)
    return out


# ── M5 CSP（Q1.14 × Q8.15 → >>16 → Q10.13）──
def csp(x, w):
    z = []
    for j in range(N_COMP):
        acc = sum(int(w[c][j]) * int(x[c]) for c in range(N_CH))
        z.append(sat(acc >> 16, 24))
    return z


def main():
    LUT = gen_lut()
    G.LUT = LUT

    # ── ① 参数（用真实训练结果）──
    J = json.load(open(os.path.join(ROOT, "params", "csp_svm_params.json"),
                       encoding="utf-8"))
    SEL = list(np.array(J["mibif_sel_idx"]).ravel())      # MIBIF 选中的特征下标
    sos = np.array(J["iir"]["sos_q"], dtype=np.int64)      # (3,2,5)
    cspw_raw = np.array(J["csp_W"], dtype=np.int64)        # (N_BAND, N_CH, N_COMP)
    svmw_raw = np.array(J["svm"]["w"], dtype=np.int64)
    svmb_raw = int(J["svm"]["b"])

    # ★★ CSP 权重的**每带归一化**（与 zynq/ps/ea.c 的 ea_quantize_w 完全一致）
    #
    #   JSON 里导出的是**折叠后的 Q3.14 / 18 位**权重（csp_W_bits=18）。
    #   但 EA 折叠会把动态范围抬到 |W_eff|max ≈ 19（8 通道）或 3.81（3 通道），
    #   直接塞进 16 位会**静默截断** —— 本测试第一版就是这样，
    #   现象是 z 最大差 212、决策值全错，但标签还能碰巧对上大半。
    #
    #   解法利用特征对整体缩放的免疫性：
    #       f_i = ln(var_i / Σ var_j)  对 W → αW 完全不变
    #   所以每个带各自缩放到峰值 0.875，再量化到 Q1.14 / 16 位。
    Q14 = 1 << 14
    cspw = np.zeros_like(cspw_raw)
    for b in range(cspw_raw.shape[0]):
        mx = int(np.abs(cspw_raw[b]).max())
        if mx == 0:
            continue
        # cspw_raw 已经是 Q3.14，所以直接按比例缩到 0.875
        cspw[b] = np.round(cspw_raw[b] * (0.875 * Q14 / mx)).astype(np.int64)
        # 注意：这是 Q1.14 的**整数值**，PL 侧只认这个
    wmax = int(np.abs(cspw).max())
    print(f"[归一化] CSP 权重每带缩放到峰值 0.875（Q1.14/16bit）："
          f"原 |W|max={int(np.abs(cspw_raw).max())}(Q3.14) → 现 {wmax}"
          f"（上限 32767，余量 {(1-wmax/32767)*100:.0f}%）")
    if wmax >= 32767:
        raise SystemExit("✗ CSP 权重超出 Q1.14 —— 检查归一化")

    # ★★ 特征标准化的折叠（本项目第三次用同一个原理）
    #
    #   训练好的 SVM 吃的是**标准化后**的特征：
    #       d = Σ w_i·(f_i − mu_i)/sd_i + b
    #   把它展开：
    #       d = Σ (w_i/sd_i)·f_i + ( b − Σ w_i·mu_i/sd_i )
    #         = Σ w'_i·f_i + b'
    #   于是 PL 侧**完全不需要知道标准化这一步** —— 零改动、零资源。
    #
    #   （前两次：① EA 白化折进 CSP 投影 W_eff = MᵀW̃；
    #             ② 线性 SVM 折叠成 w·f + b，不用存支持向量。）
    #
    #   ⚠️ 标度不能算错：w、sd 都是 Q4.11，所以
    #        w'_q11 = round( w_raw * 2048 / sd_q11 )
    #      漏掉 ×2048 会让量化后只剩 ±7，精度全丢（踩过一次）。
    mu = np.array(J["feat_norm"]["mu_q11"], dtype=np.int64)
    sd = np.array(J["feat_norm"]["sd_q11"], dtype=np.int64)
    sd = np.maximum(sd, 1)
    Q11 = 1 << 11
    svmw = np.round(svmw_raw * Q11 / sd).astype(np.int64)
    svmb = int(round(svmb_raw - np.sum(svmw_raw * mu / sd)))
    wmax = int(np.abs(svmw).max())
    print(f"[折叠] 特征标准化 → SVM 权重："
          f"|w|max {int(np.abs(svmw_raw).max())} → {wmax}"
          f"（Q4.11 上限 32767，余量 {(1-wmax/32767)*100:.0f}%）")
    if wmax >= 32767:
        raise SystemExit("✗ 折叠后权重超出 Q4.11 —— 需要加宽 SVM 权重位宽")
    if list(np.array(J["mibif_sel_idx"]).ravel()) != SEL:
        print(f"  ⚠️ MIBIF 索引与 RTL 里的硬编码不一致："
              f"JSON={list(np.array(J['mibif_sel_idx']).ravel())} RTL={SEL}")

    svmw = svmw.astype(np.int64)
    print(f"[参数] N_BAND={sos.shape[0]} N_CH={cspw.shape[1]} N_COMP={cspw.shape[2]} "
          f"FEAT_DIM={len(svmw)} b={svmb}")

    # ── ② 激励 ──
    data = open(os.path.join(ROOT, "tools", "sample_stream.bin"), "rb").read()
    n_pkt = len(data) // 33
    with open(os.path.join(HERE, "ff_bytes.txt"), "w") as f:
        for b in data:
            f.write(f"{b}\n")
    with open(os.path.join(HERE, "ff_sos.txt"), "w") as f:
        for v in sos.reshape(-1):
            f.write(f"{v}\n")
    with open(os.path.join(HERE, "ff_csp.txt"), "w") as f:
        for v in cspw.reshape(-1):
            f.write(f"{v}\n")
    with open(os.path.join(HERE, "ff_svmw.txt"), "w") as f:
        for v in svmw:
            f.write(f"{v}\n")
    with open(os.path.join(HERE, "ff_svmb.txt"), "w") as f:
        f.write(f"{svmb}\n")
    print(f"[激励] {len(data)} 字节 / {n_pkt} 包")

    # ── ③ 跑仿真 ──
    vvp = os.path.join(HERE, "tb_ff.vvp")
    srcs = [os.path.join(RTL, m) for m in
            ("uart_rx.v", "pkt_parser.v", "dc_block.v", "biquad_bank.v",
             "csp_project.v", "win_var.v", "lognorm.v", "svm_core.v",
             "axil_regs.v", "clk_pll.v", "eeg_csp_svm_top.v")]
    # ★ -DSIMULATION：clk_pll 在仿真下走行为模型（不实例化 MMCME2_BASE 原语）
    cmd = ["iverilog", "-g2012", "-DSIMULATION",
           f"-DN_BYTE={len(data)}", f"-DN_PKT={n_pkt}",
           "-o", vvp, "tb_fullflow.v"] + srcs
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout); print(r.stderr)
        raise SystemExit("iverilog 编译失败")
    print("[仿真] 编译通过，运行中（含 AXI BFM）...")
    r = subprocess.run(["vvp", vvp], cwd=HERE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    with open(os.path.join(HERE, "ff_log.txt"), "w", encoding="utf-8") as f:
        f.write((r.stdout or "") + (r.stderr or ""))
    for line in (r.stdout or "").splitlines():
        if line.startswith("[ff]"):
            print("  " + line)
    if r.returncode != 0:
        print((r.stdout or "")[-3000:]); print((r.stderr or "")[-2000:])
        raise SystemExit("仿真失败")

    # ── ④ 逐位参考流水线 ──
    print("\n[参考] 逐位复算整条链 ...")
    # 解析出通道 0..2
    ch = np.zeros((n_pkt, N_CH), dtype=np.int64)
    for i in range(n_pkt):
        for c in range(N_CH):
            v = (data[i*33 + 2 + 3*c] << 16) | (data[i*33 + 3 + 3*c] << 8) | data[i*33 + 4 + 3*c]
            ch[i, c] = v - (1 << 24) if v & 0x800000 else v

    # dc + 3 带滤波
    band = np.zeros((n_pkt, N_BAND, N_CH), dtype=np.int64)
    for c in range(N_CH):
        xd = dc_run(list(ch[:, c]))
        for b in range(N_BAND):
            band[:, b, c] = biquad(xd, list(sos[b, 0]))     # 节 0
            band[:, b, c] = biquad(list(band[:, b, c]), list(sos[b, 1]))  # 节 1

    # csp → win_var → lognorm → svm
    z = np.zeros((n_pkt, N_BAND * N_COMP), dtype=np.int64)
    for t in range(n_pkt):
        for b in range(N_BAND):
            zz = csp(band[t, b, :], cspw[b])
            z[t, b*N_COMP:(b+1)*N_COMP] = zz

    dec = []
    sz_all = []          # 每个决策点的窗和（供逐级比对）
    f_all = []           # 每个决策点的特征
    ring = np.zeros((WIN_LEN, N_BAND*N_COMP), dtype=np.int64)
    sz = np.zeros(N_BAND*N_COMP, dtype=np.int64)
    sz2 = np.zeros(N_BAND*N_COMP, dtype=np.int64)
    wp = 0
    for t in range(n_pkt):
        if t >= WIN_LEN:
            old = ring[wp]
            sz -= old
            sz2 -= old * old
        ring[wp] = z[t]
        sz += z[t]
        sz2 += z[t] * z[t]
        wp = (wp + 1) % WIN_LEN
        if t >= WIN_LEN - 1:
            f = np.array(lognorm_ref(list(sz), list(sz2), WIN_LEN), dtype=np.int64)
            fs = f[SEL]
            # svm_core: Q4.11 × Q4.11 = Q8.22 → <<1 → Q8.23，再加偏置
            d = int((fs * svmw).sum()) << 1
            d = sat(d + svmb, 32)
            dec.append((1 if d >= 0 else 0, d))
            sz_all.append(sz.copy())
            f_all.append(f.copy())
    dec = np.array(dec, dtype=np.int64)
    np.savetxt(os.path.join(HERE, "ff_reference_dec.txt"), dec, fmt="%d")
    sz_all = np.array(sz_all, dtype=np.int64)
    f_all = np.array(f_all, dtype=np.int64)
    print(f"[参考] 参考决策数 = {len(dec)}（第 {WIN_LEN} 包起）")

    # ── ⑤ 比对 ──
    rtl = np.loadtxt(os.path.join(HERE, "ff_dec.txt"), dtype=np.int64)
    rtl = rtl.reshape(-1, 2)
    print(f"[RTL ] RTL 决策数 = {len(rtl)}")

    # ── 中间量逐级比对（定位发散点用）──
    def _cmp(name, rtlfile, refarr, rtl_off=0, ref_off=0):
        try:
            a = np.loadtxt(os.path.join(HERE, rtlfile), dtype=np.int64)
        except Exception as ex:
            print(f"  {name}: 读取失败 {ex}"); return None
        a = np.atleast_2d(a)
        # ★ RTL 每个采样点都出一次决策，参考只在窗口填满后算，
        #   所以两者行号差 (WIN_LEN-1)，必须各自给偏移，不能只偏移一边。
        a = a[rtl_off:]
        b = refarr[ref_off:ref_off+len(a)]
        n2 = min(len(a), len(b))
        a = a[:n2]; b = b[:n2]
        if n2 == 0:
            print(f"  {name:10s} 无可比数据（对齐后为空）"); return None
        d = np.abs(a - b).max()
        print(f"  {name:10s} 最大差 = {d}  {'✓' if d == 0 else '✗'}")
        return d

    print("\n" + "=" * 74)
    print(" 中间量逐级比对（找第一处发散）")
    print("=" * 74)
    if os.path.exists(os.path.join(HERE, "ff_z.txt")):
        _cmp("z (CSP)", "ff_z.txt", z)
        _cmp("Sz (窗和)", "ff_sz.txt", sz_all, WIN_LEN-1, 0)
        _cmp("f (特征)", "ff_f.txt", f_all[:, SEL], WIN_LEN-1, 0)

    print("\n" + "=" * 74)
    print(" 全流程对拍")
    print("=" * 74)
    n = min(len(rtl), len(dec))
    ok = True
    if n == 0:
        print("  ✗ 没有可比对的决策")
        ok = False
    else:
        # ★ 索引对齐：RTL 每个采样点都出一次决策（lognorm 每个 sample_valid 都启动），
        #   而参考只在窗口填满后才算，所以 RTL 的第 (WIN_LEN-1) 个决策
        #   才对应参考的第 0 个。不偏移会整体错位。
        #   （第一版忘了偏移，还留着旧的 >>1，两处叠加导致 119/119 全不一致）
        # ★ 还要再 +1：TB 在 dec_valid 那一拍 dump，而 svm_core 的 d
        #   要到**下一拍**才更新，所以 ff_dec 第 i 行是第 i−1 个样本的决策。
        #   ff_f / ff_z / ff_sz 都是组合可见的，不需要这个 +1。
        r = rtl[WIN_LEN:WIN_LEN+n]
        c = dec[:len(r)]
        lb_mismatch = int((r[:, 0] != c[:, 0]).sum())
        d_mismatch = int((r[:, 1] != c[:, 1]).sum())
        print(f"  比较 {len(r)} 个决策（RTL 第 {WIN_LEN-1} 个起对齐）")
        print(f"  标签不一致 = {lb_mismatch}   {'✓' if lb_mismatch == 0 else '✗'}")
        print(f"  决策值不一致 = {d_mismatch}  {'✓' if d_mismatch == 0 else '✗'}")
        if d_mismatch:
            k = int(np.argmax(r[:, 1] != c[:, 1]))
            print(f"    首个不一致: RTL d={r[k,1]} vs 参考 d={c[k,1]} "
                  f"(差 {int(r[k,1]-c[k,1])})")
        ok = (lb_mismatch == 0 and d_mismatch == 0)

    print("\n" + "=" * 74)
    print(" 全流程仿真通过 ✓" if ok else " 存在不一致 ✗")
    print("=" * 74)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
