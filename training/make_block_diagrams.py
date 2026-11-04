#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成两张交付图：
  fig_d1_partition.png  —— PS/PL 分工与连接框图
  fig_d2_dataflow.png   —— 端到端数据流图（各段传输什么数据）

对应文档：规划/05_算法FPGA移植交接文档.md

注意：只用 Microsoft YaHei 覆盖的字形，避开 ᵀ / R̄ / W̃ / ⚠ 等组合字符
（matplotlib 会把它们渲染成方框 tofu）。
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "规划", "figures")
os.makedirs(OUT, exist_ok=True)

C_PL, C_PL_E = "#dbeafe", "#1d4ed8"
C_PS, C_PS_E = "#dcfce7", "#15803d"
C_EXT, C_EXT_E = "#fef3c7", "#b45309"
C_WARN, C_WARN_E = "#fee2e2", "#b91c1c"
C_PUR, C_PUR_E = "#ede9fe", "#6d28d9"
C_CYA, C_CYA_E = "#cffafe", "#0e7490"


def box(ax, x, y, w, h, fc, ec, lw=1.3, r=0.25, z=2, ls="-"):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle=f"round,pad=0,rounding_size={r}",
                                fc=fc, ec=ec, lw=lw, zorder=z, linestyle=ls))


def txt(ax, x, y, t, fs=9, c="#1e293b", bold=False, ha="center", va="center",
        z=5, rot=0, **kw):
    ax.text(x, y, t, fontsize=fs, color=c, ha=ha, va=va, zorder=z, rotation=rot,
            fontweight="bold" if bold else "normal", linespacing=1.5, **kw)


def arrow(ax, p1, p2, color="#334155", lw=2.0, ls="-", style="-|>"):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, color=color, lw=lw,
                                 linestyle=ls, mutation_scale=15, zorder=3))


# ══════════════════════════════════════════════════════════════════
#  图 1：PS / PL 分工与连接框图
# ══════════════════════════════════════════════════════════════════
def fig1():
    fig, ax = plt.subplots(figsize=(17.0, 11.6))
    ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")

    # ── 采集端 ──
    box(ax, 1.5, 91.5, 42, 7.5, C_EXT, C_EXT_E, 1.6)
    txt(ax, 22.5, 96.4, "OpenBCI Cyton（锂电池供电，与被试隔离）", 11, C_EXT_E, True)
    txt(ax, 22.5, 93.6, "ADS1299（8ch / 24bit / 250Hz）→ PIC32 → RFduino", 9, "#78350f")

    box(ax, 46.5, 91.5, 24, 7.5, "#f1f5f9", "#94a3b8", 1.3)
    txt(ax, 58.5, 95.2, "RFduino Dongle", 10, "#475569", True)
    txt(ax, 58.5, 92.8, "2.4GHz 无线（命令通道）", 8.5, "#64748b")

    # ── 隔离器 ──
    box(ax, 1.5, 81.0, 42, 6.8, C_WARN, C_WARN_E, 1.6)
    txt(ax, 22.5, 85.4, "数字隔离器（ADuM1201 / Si8620）", 10, C_WARN_E, True)
    txt(ax, 22.5, 82.9, "USB 供电的 FPGA 板会给被试建立到市电地的通路", 8.3, "#7f1d1d")

    arrow(ax, (9, 91.5), (9, 87.8), "#b45309", 2.2)
    txt(ax, 16.0, 89.7, "D11 (TX) / D12 (RX) / GND", 8, "#78350f", ha="left")
    txt(ax, 16.0, 88.7, "3.3V TTL", 8, "#78350f", ha="left")

    arrow(ax, (22.5, 81.0), (22.5, 78.0), C_WARN_E, 2.6)
    txt(ax, 34.5, 79.5, "UART 115200 8N1｜33 字节包 @250Hz", 8.6, C_WARN_E, True)

    # ── Zynq 外框 ──
    box(ax, 1.5, 3.0, 97, 73.5, "#f8fafc", "#475569", 2.2, 0.6, z=1)
    txt(ax, 50, 74.0, "Zynq-7020（PYNQ-Z2 class）", 13, "#0f172a", True, z=4)

    # ── PL ──
    box(ax, 3.5, 6.0, 48, 65.0, C_PL, C_PL_E, 2.2, 0.5)
    txt(ax, 27.5, 68.3, "PL（FPGA Fabric）—— 实时推理链", 12, C_PL_E, True)
    txt(ax, 27.5, 65.6, "DSP ~20 ｜ BRAM ~11 ｜ 利用率 ~13-16% ｜ PL 内延迟 ~4 us",
        8.6, "#475569", style="italic")

    pl_rows = [
        ("M1  uart_rx",      "异步串口接收 · 位周期 868 时钟 @100MHz"),
        ("M2  pkt_parser",   "搜 0x41 → 收 33 字节 → 校验包尾 (0xC0|type)"),
        ("M3  dc_block",     "一阶高通 0.5 Hz（去掉 ADS1299 直流偏移）"),
        ("M4  biquad_bank",  "3 子带 x 2 节 转置直接II型（8-16/16-24/24-32Hz）"),
        ("M5  csp_project",  "z = W_eff^T x，3 带并行；W_eff 已含 EA 折叠"),
        ("M6  win_var",      "滑动窗加新减旧 Sum z / Sum z^2（6 路，T=626）"),
        ("M7  lognorm",      "归一化对数方差 → f[6]（512 点 log2 查表）"),
        ("M8  svm_core",     "取 MIBIF 选中 4 维 → d = w·f + b → 标签 + 决策值"),
        ("M9  axil_regs",    "AXI-Lite 寄存器 / 参数 RAM / 错误计数"),
    ]
    y = 61.5
    for name, desc in pl_rows:
        box(ax, 5.0, y, 45, 5.0, "white", C_PL_E, 1.1, 0.15)
        txt(ax, 6.3, y + 3.3, name, 9.6, C_PL_E, True, ha="left")
        txt(ax, 6.3, y + 1.4, desc, 8.2, "#475569", ha="left")
        if y > 12:
            arrow(ax, (27.5, y), (27.5, y - 0.95), C_PL_E, 1.5)
        y -= 6.05

    # ── PS ──
    box(ax, 55.0, 6.0, 42, 65.0, C_PS, C_PS_E, 2.2, 0.5)
    txt(ax, 76.0, 68.3, "PS（ARM Cortex-A9 / Linux）", 12, C_PS_E, True)
    txt(ax, 76.0, 65.6, "不参与实时数据通路，只做控制 / 校准 / 交互", 8.6, "#475569",
        style="italic")

    ps_rows = [
        ("(1) 校准流程（EA）", "20 试次 → R_bar → M；折叠 W_eff = M^T·W"),
        ("(2) 参数管理",       "下发 W_eff / w / b / SOS / MIBIF 索引 + CRC 回读"),
        ("(3) 结果读取",       "轮询 DECISION 寄存器 → 标签 + 决策值"),
        ("(4) 可视化",         "z 波形（展示空间滤波在干活）/ 特征 / d(t) 曲线"),
        ("(5) 数据记录",       "原始 + 特征 + 决策 → SD 卡（独立线程）"),
        ("(6) 范式呈现（cue）", "屏幕 / LED 时序；与数据同源，免硬件触发同步"),
        ("(7) 状态监控",       "丢包数 / 错误标志 / PERF_CNT（实测延迟证据）"),
    ]
    y = 61.5
    for name, desc in ps_rows:
        box(ax, 56.5, y, 39, 5.0, "white", C_PS_E, 1.1, 0.15)
        txt(ax, 57.8, y + 3.3, name, 9.6, C_PS_E, True, ha="left")
        txt(ax, 57.8, y + 1.4, desc, 8.2, "#475569", ha="left")
        y -= 6.05

    # ── PL ↔ PS 连接 ──
    xa, xb = 51.8, 54.8
    txt(ax, 53.3, 62.0, "AXI", 8.5, "#64748b", True, rot=90)

    arrow(ax, (xa, 50.0), (xb, 50.0), C_PUR_E, 2.4)
    txt(ax, 53.3, 53.8, "AXI-Lite 写", 8, C_PUR_E, True, rot=90)
    txt(ax, 53.3, 43.5, "参数下发\n控制\n软复位", 7.6, C_PUR_E)

    arrow(ax, (xb, 30.0), (xa, 30.0), C_CYA_E, 2.4)
    txt(ax, 53.3, 33.8, "AXI-Lite 读", 8, C_CYA_E, True, rot=90)
    txt(ax, 53.3, 23.5, "标签+决策值\n(250Hz 轮询)\n错误计数", 7.6, C_CYA_E)

    arrow(ax, (xb, 13.0), (xa, 13.0), "#94a3b8", 1.6, ls="--")
    txt(ax, 53.3, 16.6, "AXI-DMA", 8, "#64748b", True, rot=90)
    txt(ax, 53.3, 8.0, "调试旁路\n中间结果 dump\n(可关)", 7.6, "#64748b")

    txt(ax, 50, 1.0,
        "实时数据通路全程在 PL 内闭环，PS 不参与；中间结果不出 PL",
        11, C_WARN_E, True)

    fig.savefig(os.path.join(OUT, "fig_d1_partition.png"), dpi=170,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("  -> fig_d1_partition.png")


# ══════════════════════════════════════════════════════════════════
#  图 2：端到端数据流图
# ══════════════════════════════════════════════════════════════════
def fig2():
    fig, ax = plt.subplots(figsize=(18.5, 12.0))
    ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")

    txt(ax, 50, 97.5, "端到端数据流：每一段传输什么", 15, "#0f172a", True)
    txt(ax, 50, 93.0, "<--------------  250 Hz：每 4 ms 一个采样点  -------------->",
        9.5, "#64748b", style="italic")

    # ── 主管线：10 个环节，数据格式写在框内 ──
    stages = [
        ("ADS1299",         "8ch x 24bit\n250 Hz",          C_EXT,  C_EXT_E),
        ("PIC32 组包",       "33 字节\n0x41...0xC0|type",     C_EXT,  C_EXT_E),
        ("[隔离器]",         "UART\n115200 8N1",             C_WARN, C_WARN_E),
        ("M1 uart_rx",      "字节流\n86.8 us / 字节",         C_PL,   C_PL_E),
        ("M2 pkt_parser",   "x[8]\nint24 计数域",            C_PL,   C_PL_E),
        ("M3+M4\n去DC+3带",  "x_b[3]\nQ8.15 / 24bit",        C_PL,   C_PL_E),
        ("M5\ncsp_project",  "z[3带 x 2]\nQ10.13 / 24bit",   C_PL,   C_PL_E),
        ("M6 win_var",      "var[6]\n48bit 整数累加",         C_PL,   C_PL_E),
        ("M7 lognorm",      "f[6]\nQ4.11 / 16bit",           C_PL,   C_PL_E),
        ("M8 SVM",          "标签 + d\n1bit + Q8.23",        C_PS,   C_PS_E),
    ]
    n = len(stages)
    x0, gap = 1.5, 1.15
    w = (97.0 - (n - 1) * gap) / n
    ytop, hbox = 75.0, 13.5

    for i, (name, data, fc, ec) in enumerate(stages):
        x = x0 + i * (w + gap)
        box(ax, x, ytop, w, hbox, fc, ec, 1.5, 0.3)
        txt(ax, x + w/2, ytop + hbox - 3.4, name, 9.3, ec, True)
        ax.plot([x + 1.0, x + w - 1.0], [ytop + hbox - 5.9] * 2,
                color=ec, lw=0.8, alpha=0.45, zorder=4)
        txt(ax, x + w/2, ytop + 3.6, data, 7.6, "#334155")
        if i < n - 1:
            arrow(ax, (x + w, ytop + hbox/2), (x + w + gap, ytop + hbox/2),
                  "#4338ca", 1.8)

    txt(ax, 50, 71.8, "以上全部在 PL 内闭环，PS 不参与", 9.2, C_WARN_E, True)

    # ── 中间：三条交互（三列，互不重叠）──
    txt(ax, 50, 68.2, "PS 与 PL 的交互（AXI 总线）", 11, "#0f172a", True)

    cols = [
        (1.5,  31.0, C_PUR_E, "-",  C_PUR_E, "up",
         "(1) 参数下发（AXI-Lite 写）\n"
         "W_eff[3][3x2] @ Q3.14 / 18bit\n"
         "w[4] @ Q4.11  ·  b @ Q8.23\n"
         "SOS[3][2][5]  ·  MIBIF 索引[4]\n"
         "→ 换被试 / 换 session 时各一次"),
        (34.5, 30.0, C_CYA_E, "-",  C_CYA_E, "down",
         "(2) 结果回读（AXI-Lite 读）\n"
         "DECISION：标签 + 决策值 @ Q8.23\n"
         "STATUS：丢包数 · 错误标志\n"
         "PERF_CNT：PL 处理周期数\n"
         "← 250 Hz 轮询（每 4 ms）"),
        (66.5, 32.0, "#94a3b8", "--", "#94a3b8", "both",
         "(3) 调试旁路（AXI-DMA）\n"
         "z / var / f 批量 dump\n"
         "供 ILA 与 golden vector 对拍\n"
         "可关闭，不影响主通路"),
    ]
    for x, wc, col, ls, ec, direc, body in cols:
        box(ax, x, 47.5, wc, 17.5, "white", ec, 1.6, 0.3)
        for xa, lw_ in ((x + 2.4, 2.2), (x + wc - 2.4, 1.6)):
            if direc == "up":
                arrow(ax, (xa, 47.5), (xa, 65.0), col, lw_, ls=ls, style="-|>")
            elif direc == "down":
                arrow(ax, (xa, 65.0), (xa, 47.5), col, lw_, ls=ls, style="-|>")
            else:
                arrow(ax, (xa, 47.5), (xa, 65.0), col, lw_, ls=ls, style="<|-|>")
        txt(ax, x + 5.2, 56.2, body, 8.3, "#1e293b", ha="left", va="center")

    # ── PS 侧 ──
    box(ax, 1.5, 22.5, 97, 21.5, C_PS, C_PS_E, 2.0, 0.5)
    txt(ax, 50, 40.9, "PS 侧（ARM / Linux）—— 承担 PL 做不了的事", 12, C_PS_E, True)

    ps_cards = [
        ("(1) 校准（EA）",      "20 试次 → R_bar_b\nM_b = R_bar_b^(-1/2)\n折叠 W_eff = M_b^T·W\n* 必须与任务同 session"),
        ("(2) 参数管理",        "解析 JSON / 头文件\n量化 + CRC 校验\n回读确认\n失败拒绝使用"),
        ("(3) 结果读取",        "轮询 DECISION\n标签 + 决策值\n加时间戳入队"),
        ("(4)(5) 可视化/记录",  "z 波形 · 特征 · d(t)\n原始+特征+决策\n批量写 SD 卡"),
        ("(6)(7) cue / 监控",   "范式时序（屏幕/LED）\n丢包 · 错误统计\nPERF_CNT 延迟证据"),
    ]
    cw = (95.0 - 4 * 1.4) / 5
    for i, (name, body) in enumerate(ps_cards):
        x = 2.5 + i * (cw + 1.4)
        box(ax, x, 24.5, cw, 13.5, "white", C_PS_E, 1.2, 0.2)
        txt(ax, x + cw/2, 35.7, name, 9.2, C_PS_E, True)
        txt(ax, x + cw/2, 30.0, body, 7.7, "#334155")

    # ── 底部：数据量核算 ──
    box(ax, 1.5, 1.0, 97, 19.5, "#fffbeb", C_EXT_E, 1.8, 0.4)
    txt(ax, 50, 18.1, "数据量与带宽核算", 11, C_EXT_E, True)
    rows = [
        "UART 容量 11520 B/s  |  实际负载 33B x 250Hz = 8250 B/s  ->  占用 71.6%（余量 28.4%）",
        "每字节 86.8 us  |  每包 2.864 ms  |  采样周期 4 ms  ->  每周期空闲 1.136 ms",
        "PL 内计算：滤波 240 MAC + CSP 48 MAC + 窗 12 MAC = 300 MAC/采样点 = 75 kMAC/s",
        "       占 Zynq-7020（220 DSP @200MHz ~ 44 GMAC/s）的 0.0002%  ->  瓶颈是引脚/时序，不是算力",
        "PL 内延迟：样本到齐 -> 决策输出 ~ 290 周期 @100MHz ~ 3-4 us（确定·无抖动）",
        "EA 折叠带来的延迟增量 = 0（矩阵已离线乘进 W_eff，推理链路不感知）",
    ]
    for i, t in enumerate(rows):
        txt(ax, 3.5, 15.1 - i * 2.35, t, 8.3, "#422006", ha="left")

    fig.savefig(os.path.join(OUT, "fig_d2_dataflow.png"), dpi=170,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("  -> fig_d2_dataflow.png")


if __name__ == "__main__":
    print("生成交付图：")
    fig1()
    fig2()
    print(f"\n输出目录: {os.path.abspath(OUT)}")
