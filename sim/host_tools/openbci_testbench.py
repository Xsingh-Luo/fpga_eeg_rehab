#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OpenBCI 测试台 —— Cyton 实时采集 / 波形 / 链路质量 / 录制 / MI 范式 / 回放转发

这是给"台架测试"用的图形界面，和命令行版 openbci_record.py 共用采集核心。

功能
────
1. 串口模式：连接 Cyton Dongle，实时显示 8 通道波形、包率、丢包、重同步；
2. 文件模式：按 250 Hz 回放 .bin（可循环、可调速），用于没有硬件时排练；
3. 录制：串口模式自动保存 *_raw.bin / *_raw.csv / *_meta.json；
4. 运动想象：弹出全屏提示屏，按 2b 时序跑 3 s 注视 + cue + 4 s 想象 + 4 s 休息，
   事件写 *_events.csv；
5. 回放转发：把 .bin 按 250 Hz 发到另一个串口（接 Zynq PL 时用），默认把
   0xA0 帧头改写成 0x41（当前 pkt_parser.v 只认 0x41）；
6. 自检：包数、丢包、CH1-CH3 是否全 0、是否贴轨。

运行
────
  H:/FPGA2026QS/.venv/Scripts/python.exe H:/FPGA2026QS/tools/openbci_testbench.py

  # 无硬件演示（自动载入 sim/host_tools/sample_stream.bin 并循环回放）
  ... openbci_testbench.py --demo

  # 开发用：启动后截图再退出
  ... openbci_testbench.py --demo --screenshot shot.png --shot-delay 4
"""
import argparse
import ctypes
import os
import queue
import sys
import threading
import time
from collections import deque

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from scipy.signal import welch

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from cyton_parse import CytonParser, decode
from openbci_record import (Acquisition, EventLog, CH_NAMES, FS, MARK,
                            CLASS_NAME, build_mi_trials, build_mi_phases)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ── 配色 ──────────────────────────────────────────────────
BG = "#0b1220"
PANEL = "#111c2e"
CARD = "#16243a"
BORDER = "#22314a"
TEXT = "#e6edf7"
MUTED = "#8ea0b8"
ACCENT = "#2dd4bf"
ACCENT_DARK = "#0f766e"
BLUE = "#38bdf8"
DANGER = "#fb7185"
WARN = "#fbbf24"
CH_COLORS = ["#5eead4", "#7dd3fc", "#c4b5fd", "#f9a8d4",
             "#fde68a", "#86efac", "#fca5a5", "#93c5fd"]

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
try:
    sys.setswitchinterval(0.001)
except Exception:
    pass

# Windows 高 DPI：让 Tk 与截图坐标一致，否则 125% 缩放下窗口会被截掉右下角
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def safe_name(s):
    bad = '<>:"/\\|?* '
    out = "".join("_" if c in bad else c for c in str(s)).strip("_")
    return out or "gui"


def fmt_elapsed(sec):
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def list_com_ports():
    try:
        from serial.tools import list_ports
        return [p.device for p in sorted(list_ports.comports(), key=lambda p: p.device)]
    except Exception:
        return []


def session_root():
    """优先 data/raw/openbci；沙箱/只读环境退回 sessions/。"""
    for p in (os.path.join(ROOT, "data", "raw", "openbci"),
              os.path.join(ROOT, "sessions")):
        try:
            os.makedirs(p, exist_ok=True)
            probe = os.path.join(p, ".write_probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("")
            os.remove(probe)
            return p
        except Exception:
            continue
    return os.path.join(ROOT, "sessions")


# ══════════════════════════════════════════════════════════
#  文件回放源（按 250 Hz）
# ══════════════════════════════════════════════════════════
class ReplaySource:
    def __init__(self, path, on_sample, speed=1.0, loop=True,
                 forward_port=None, rewrite_header=True):
        self.path = path
        self.on_sample = on_sample
        self.speed = max(0.1, float(speed))
        self.loop = bool(loop)
        self.forward_port = forward_port or None
        self.rewrite_header = bool(rewrite_header)

        with open(path, "rb") as f:
            blob = f.read()
        parser = CytonParser(None)
        self.packets = parser.feed(blob)
        if not self.packets:
            raise ValueError(f"{path} 里没有解析出完整的 33 字节包")
        self.n_resync = parser.n_resync
        self.total = len(self.packets)

        self.error = None
        self.forward_error = None
        self.done = threading.Event()
        self.done.set()
        self.stop_evt = threading.Event()
        self.pause_evt = threading.Event()
        self.thread = None

        self.fwd = None
        self.n_pkt = 0
        self.abs_idx = -1
        self.last_marker = 0
        self.marker_count = 0
        self.t_start = 0.0
        self.t_first = 0.0
        self.next_t = 0.0

    def start(self):
        if self.forward_port:
            try:
                import serial
                self.fwd = serial.Serial(self.forward_port, 115200, timeout=0,
                                         write_timeout=1.0)
            except Exception as e:
                self.forward_error = str(e)
                self.fwd = None
        self.stop_evt.clear()
        self.pause_evt.clear()
        self.done.clear()
        self.t_start = time.perf_counter()
        self.next_t = self.t_start
        self.thread = threading.Thread(target=self._run, name="replay", daemon=True)
        self.thread.start()

    def _run(self):
        try:
            period = 1.0 / (FS * self.speed)
            while not self.stop_evt.is_set():
                for pkt in self.packets:
                    if self.stop_evt.is_set():
                        break
                    while self.pause_evt.is_set() and not self.stop_evt.is_set():
                        time.sleep(0.02)
                        self.next_t = time.perf_counter()
                    self._emit(pkt)
                    # 和 cyton_sim 一样：粗睡 + 自旋，保证 250 Hz 不是“大概 200 Hz”
                    self.next_t += period
                    now = time.perf_counter()
                    if now < self.next_t:
                        # sleep(0) 让出调度但不引入 Windows 的 ~15 ms 睡眠粒度
                        while self.next_t - time.perf_counter() > 0.001:
                            time.sleep(0)
                        while time.perf_counter() < self.next_t:
                            pass
                    elif now - self.next_t > 0.2:
                        # 只丢弃过大的欠账（暂停/系统卡死），小抖动下一包补回来
                        self.next_t = now
                if not self.loop:
                    break
        except Exception as e:
            self.error = e
        finally:
            self.done.set()
            if self.fwd is not None:
                try:
                    self.fwd.close()
                except Exception:
                    pass
                self.fwd = None

    def _emit(self, pkt):
        vals, seq, footer, aux, _ = decode(pkt)
        marker = 0
        if footer in (0xC1, 0xC2):
            marker = (int(aux[0]) << 8) | int(aux[1])
            if marker != self.last_marker:
                self.marker_count += 1
                self.last_marker = marker

        if self.n_pkt == 0:
            self.t_first = time.perf_counter()
        self.abs_idx += 1
        self.n_pkt += 1

        if self.fwd is not None:
            out = pkt
            if self.rewrite_header and pkt[0] == 0xA0:
                out = bytes([0x41]) + pkt[1:]
            try:
                self.fwd.write(out)
            except Exception as e:
                self.forward_error = str(e)
                try:
                    self.fwd.close()
                except Exception:
                    pass
                self.fwd = None

        self.on_sample(self.abs_idx, seq, vals, marker, footer)

    def stop(self):
        self.stop_evt.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        if self.fwd is not None:
            try:
                self.fwd.close()
            except Exception:
                pass
            self.fwd = None
        self.done.set()

    def pause(self):
        self.pause_evt.set()

    def resume(self):
        self.pause_evt.clear()

    def send_marker(self, _value):
        return False

    def estimate_sample(self):
        return self.abs_idx, self.abs_idx

    def stats(self):
        base = self.t_first or self.t_start
        el = max(1e-3, time.perf_counter() - base) if base else 0.0
        return {
            "packets": self.n_pkt,
            "samples": max(0, self.abs_idx + 1),
            "rate": (self.n_pkt / el) if el else 0.0,
            "lost": 0,
            "loss": 0.0,
            "resync": self.n_resync,
            "elapsed": el,
            "markers": self.marker_count,
            "done": self.done.is_set(),
            "error": self.error,
            "paused": self.pause_evt.is_set(),
            "forward_error": self.forward_error,
            "forwarding": self.fwd is not None,
        }


# ══════════════════════════════════════════════════════════
#  MI 提示屏（Toplevel，不另开 mainloop）
# ══════════════════════════════════════════════════════════
class CueRunner:
    def __init__(self, master, source, out_dir, name, params, on_log, on_done=None):
        self.source = source
        self.on_log = on_log
        self.on_done = on_done
        self.out_dir = out_dir
        self.name = name
        self.closed = False
        self.idx = 0
        self.cur = None

        self.trials = build_mi_trials(params["trials_per_class"], params["blocks"])
        self.phases = build_mi_phases(
            self.trials, params["blocks"], params["fixation"], params["cue"],
            params["imagery"], params["rest"], params["rest_jitter"],
            params["block_rest"])
        self.events = EventLog(os.path.join(out_dir, f"{name}_events.csv"))

        self.win = tk.Toplevel(master)
        self.win.title("MI 提示屏 - Esc 中止")
        self.win.configure(bg="black")
        if params.get("windowed"):
            self.win.geometry("1100x720+80+40")
        else:
            self.win.attributes("-fullscreen", True)
        self.canvas = tk.Canvas(self.win, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.win.update_idletasks()
        self.win.bind("<Escape>", lambda e: self.abort())
        self.win.protocol("WM_DELETE_WINDOW", self.abort)
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass
        self.win.lift()
        self.win.focus_force()
        self.on_log(f"MI 开始：{len(self.trials)} trial，{params['blocks']} 组；事件 {name}_events.csv")
        self.win.after(250, self._next)

    def _next(self):
        if self.closed:
            return
        err = getattr(self.source, "error", None) if self.source else None
        if err is not None:
            self._show_error(f"数据源错误：{err}")
            return
        if self.idx >= len(self.phases):
            self._finish()
            return
        phase, dur, trial, block = self.phases[self.idx]
        self.idx += 1
        self._on_phase(phase, trial, block)
        self.win.after(max(1, int(round(dur * 1000.0))), self._next)

    def _on_phase(self, phase, trial, block):
        if phase == "block_start":
            self.cur = None
            self._clear()
            self._text(f"第 {block + 1} / {self._blocks()} 组\n\n准备…", 34)
            self._event("block_start", MARK["block"], block=block)
        elif phase == "trial_start":
            self.cur = dict(trial)
            self._clear()
            self._cross()
            self._event("trial_start", MARK["trial"], block=block)
        elif phase == "cue":
            self._clear()
            self._arrow(trial["label"])
            self._event("cue", MARK["left"] if trial["label"] == 0 else MARK["right"],
                        block=block)
        elif phase == "imagery":
            self._clear()
            self._cross()
        elif phase == "rest":
            self._event("imagery_end", 0, block=block)
            self.cur = None
            self._clear()
        elif phase == "block_end":
            self.cur = None
            self._clear()
            self._text("本组结束\n\n休息", 34)
            self._event("block_end", MARK["block"], block=block)

    def _blocks(self):
        return max(1, len({t["block"] for t in self.trials}))

    def _event(self, name, marker, block=None):
        t = self.cur or {}
        label = t.get("label", "")
        cls = CLASS_NAME.get(label, "") if label != "" else ""
        sample = None
        if self.source is not None:
            try:
                sample = self.source.estimate_sample()[0]
            except Exception:
                sample = None
        sent = False
        if marker and self.source is not None:
            try:
                sent = bool(self.source.send_marker(marker))
            except Exception:
                sent = False
        try:
            self.events.add(t.get("idx", ""), t.get("block", "" if block is None else block),
                            label, cls, name, sample, sent)
        except Exception:
            pass
        if name in ("trial_start", "cue", "imagery_end", "block_start", "block_end"):
            self.on_log(f"T{t.get('idx', '-')} {cls or ''} {name} sample={sample}"
                        + (" marker" if sent else ""))

    def _clear(self):
        self.canvas.delete("all")

    def _size(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 100 or h < 100:
            w, h = self.canvas.winfo_screenwidth(), self.canvas.winfo_screenheight()
        return w, h

    def _text(self, s, size=28, color="#e5e7eb"):
        w, h = self._size()
        self.canvas.create_text(w // 2, h // 2, text=s, fill=color,
                                font=("Microsoft YaHei", size), width=w * 0.82,
                                justify="center")

    def _cross(self):
        w, h = self._size()
        cx, cy = w // 2, h // 2
        s = min(w, h) * 0.045
        self.canvas.create_line(cx - s, cy, cx + s, cy, fill="#9ca3af", width=3)
        self.canvas.create_line(cx, cy - s, cx, cy + s, fill="#9ca3af", width=3)

    def _arrow(self, label):
        w, h = self._size()
        cx, cy = w // 2, h // 2
        L = min(w, h) * 0.24
        color = "#5eead4"
        lw = max(8, int(h * 0.014))
        if label == 0:
            tip, base = cx - L, cx - L * 0.52
            self.canvas.create_line(cx + L * 0.42, cy, base, cy,
                                    fill=color, width=lw, capstyle="round")
            self.canvas.create_polygon(tip, cy, base, cy - h * 0.10,
                                       base, cy + h * 0.10, fill=color, outline="")
            txt = "左手"
        else:
            tip, base = cx + L, cx + L * 0.52
            self.canvas.create_line(cx - L * 0.42, cy, base, cy,
                                    fill=color, width=lw, capstyle="round")
            self.canvas.create_polygon(tip, cy, base, cy - h * 0.10,
                                       base, cy + h * 0.10, fill=color, outline="")
            txt = "右手"
        self.canvas.create_text(cx, cy + h * 0.19, text=txt, fill="#cbd5e1",
                                font=("Microsoft YaHei", 34))

    def _finish(self):
        self._clear()
        self._text("实验结束\n\n感谢配合", 36)
        self.on_log("MI 结束")
        self.win.after(2500, self._close)

    def _show_error(self, msg):
        self._clear()
        self._text(msg, 22, DANGER)
        self.on_log(msg)
        self.win.after(3500, self._close)

    def abort(self):
        if not self.closed:
            self.on_log("MI 已中止")
        self._close()

    def _close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.events.close()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_done is not None:
            try:
                self.on_done()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════
#  主界面
# ══════════════════════════════════════════════════════════
class TestBench:
    def __init__(self, root, demo=False, screenshot=None, shot_delay=4.0):
        self.root = root
        self.root.title("OpenBCI 测试台 · Cyton / MI / 回放")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = min(1500, max(1180, sw - 100))
        h = min(900, max(720, sh - 120))
        self.root.geometry(f"{w}x{h}+30+20")
        self.root.minsize(1180, 720)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.q = queue.Queue()
        self.source = None
        self.kind = None
        self.cue = None
        self.rec_dir = None
        self.rec_name = None
        self.last_idx = -1
        self.last_marker = 0
        self.last_stat_t = 0.0
        self.last_tree_t = 0.0
        self.last_error_logged = None

        self.samples = deque(maxlen=int(5 * FS))
        self.spec_samples = deque(maxlen=int(4 * FS))

        self.mode_var = tk.StringVar(value="serial")
        self.port_var = tk.StringVar()
        self.file_var = tk.StringVar()
        self.name_var = tk.StringVar(value="gui_" + time.strftime("%Y%m%d_%H%M%S"))
        self.save_csv_var = tk.BooleanVar(value=True)
        self.hw_marker_var = tk.BooleanVar(value=False)
        self.speed_var = tk.DoubleVar(value=1.0)
        self.loop_var = tk.BooleanVar(value=True)
        self.forward_var = tk.StringVar(value="（不转发）")
        self.rewrite_var = tk.BooleanVar(value=True)
        self.range_var = tk.DoubleVar(value=150.0)
        self.auto_range_var = tk.BooleanVar(value=True)
        self.dc_var = tk.BooleanVar(value=True)

        self.mi_vars = {
            "trials_per_class": tk.IntVar(value=10),
            "blocks": tk.IntVar(value=3),
            "fixation": tk.DoubleVar(value=3.0),
            "cue": tk.DoubleVar(value=1.25),
            "imagery": tk.DoubleVar(value=4.0),
            "rest": tk.DoubleVar(value=4.0),
            "rest_jitter": tk.DoubleVar(value=1.0),
            "block_rest": tk.DoubleVar(value=20.0),
        }
        self.cue_windowed = tk.BooleanVar(value=False)

        self._style()
        self._build()
        self.refresh_ports()
        self._mode_changed()
        self._pump()
        self._render_wave()
        self._render_spec()

        if demo:
            self.root.after(350, self.start_demo)
        if screenshot:
            self.root.after(int(shot_delay * 1000), lambda: self._screenshot(screenshot))

    # ── 样式 ──────────────────────────────────────────────
    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                     padding=(16, 7), font=("Microsoft YaHei", 10))
        st.map("TNotebook.Tab", background=[("selected", CARD)],
               foreground=[("selected", ACCENT)])
        st.configure("Dark.TCombobox", fieldbackground=CARD, background=CARD,
                     foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, padding=4)
        st.map("Dark.TCombobox", fieldbackground=[("readonly", CARD)],
               foreground=[("readonly", TEXT)])
        st.configure("Dark.TSpinbox", fieldbackground=CARD, background=CARD,
                     foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, padding=3)
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                     foreground=TEXT, rowheight=23, borderwidth=0,
                     font=("Microsoft YaHei", 9))
        st.configure("Treeview.Heading", background=CARD, foreground=MUTED,
                     font=("Microsoft YaHei", 9), borderwidth=0)
        st.map("Treeview", background=[("selected", CARD)])
        st.configure("Dark.Vertical.TScrollbar", background=CARD,
                     troughcolor=PANEL, arrowcolor=MUTED, bordercolor=PANEL)

    def _card(self, title):
        f = tk.Frame(self.left, bg=PANEL, highlightbackground=BORDER,
                     highlightthickness=1)
        f.pack(fill="x", pady=(0, 10))
        tk.Label(f, text=title, bg=PANEL, fg=MUTED,
                 font=("Microsoft YaHei", 9, "bold"), anchor="w").pack(
            fill="x", padx=12, pady=(8, 4))
        body = tk.Frame(f, bg=PANEL)
        body.pack(fill="x", padx=12, pady=(0, 10))
        return body

    def _btn(self, parent, text, cmd, bg=ACCENT, fg="#042f2e", width=None):
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                      activebackground=ACCENT_DARK, activeforeground="#ffffff",
                      relief="flat", bd=0, padx=10, pady=5,
                      font=("Microsoft YaHei", 9, "bold"), cursor="hand2")
        if width:
            b.configure(width=width)
        return b

    def _label(self, parent, text, color=TEXT, size=9, bold=False):
        return tk.Label(parent, text=text, bg=PANEL, fg=color,
                        font=("Microsoft YaHei", size, "bold" if bold else "normal"),
                        anchor="w")

    # ── 布局 ──────────────────────────────────────────────
    def _build(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=18, pady=(12, 6))
        tk.Label(header, text="OpenBCI 测试台", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei", 19, "bold")).pack(side="left")
        tk.Label(header, text="  Cyton 8ch · 250 Hz · 波形 / 链路 / 录制 / MI / 回放",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei", 10)).pack(side="left", padx=(8, 0))
        self.status_pill = tk.Label(header, text="● 未连接", bg=CARD, fg=MUTED,
                                    padx=14, pady=5, font=("Microsoft YaHei", 10, "bold"))
        self.status_pill.pack(side="right")

        self._build_log(self.root)

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        # 左侧可滚动控制栏
        left_outer = tk.Frame(main, bg=BG, width=420)
        left_outer.pack(side="left", fill="y", padx=(0, 14))
        left_outer.pack_propagate(False)
        self.left_canvas = tk.Canvas(left_outer, bg=BG, highlightthickness=0, width=400)
        self.left_canvas.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(left_outer, orient="vertical", command=self.left_canvas.yview,
                           style="Dark.Vertical.TScrollbar")
        sb.pack(side="right", fill="y")
        self.left_canvas.configure(yscrollcommand=sb.set)
        self.left = tk.Frame(self.left_canvas, bg=BG)
        self.left.bind("<Configure>",
                       lambda e: self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all")))
        self.left_canvas.create_window((0, 0), window=self.left, anchor="nw", width=395)
        self.left_canvas.bind_all("<MouseWheel>", self._on_wheel)

        self._build_source_card()
        self._build_stats_card()
        self._build_channel_card()
        self._build_record_card()
        self._build_mi_card()
        self._build_replay_card()

        right = tk.Frame(main, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)
        self._build_wave_tab()
        self._build_spec_tab()



    def _on_wheel(self, event):
        try:
            w = self.left_canvas.winfo_containing(event.x_root, event.y_root)
            while w is not None:
                if w is self.left_canvas or w is self.left:
                    self.left_canvas.yview_scroll(int(-event.delta / 120), "units")
                    break
                w = getattr(w, "master", None)
        except Exception:
            pass

    def _build_source_card(self):
        b = self._card("1 · 数据源")
        row = tk.Frame(b, bg=PANEL)
        row.pack(fill="x")
        for text, val in (("串口 Cyton", "serial"), ("文件回放", "file")):
            tk.Radiobutton(row, text=text, variable=self.mode_var, value=val,
                           command=self._mode_changed, bg=PANEL, fg=TEXT,
                           selectcolor=CARD, activebackground=PANEL,
                           activeforeground=TEXT, font=("Microsoft YaHei", 9),
                           highlightthickness=0).pack(side="left", padx=(0, 12))

        prow = tk.Frame(b, bg=PANEL)
        prow.pack(fill="x", pady=(6, 2))
        self._label(prow, "端口", MUTED).pack(side="left")
        self.port_combo = ttk.Combobox(prow, textvariable=self.port_var, width=13,
                                       state="readonly", style="Dark.TCombobox")
        self.port_combo.pack(side="left", padx=6)
        self.refresh_btn = tk.Button(prow, text="刷新", command=self.refresh_ports,
                                     bg=CARD, fg=TEXT, relief="flat", bd=0,
                                     padx=8, pady=3, font=("Microsoft YaHei", 8),
                                     cursor="hand2")
        self.refresh_btn.pack(side="left")
        tk.Checkbutton(b, text="启用硬件 AUX marker（固件需支持 /4）",
                       variable=self.hw_marker_var, bg=PANEL, fg=MUTED,
                       selectcolor=CARD, activebackground=PANEL,
                       activeforeground=TEXT, font=("Microsoft YaHei", 8),
                       highlightthickness=0).pack(anchor="w", pady=(2, 0))

        frow = tk.Frame(b, bg=PANEL)
        frow.pack(fill="x", pady=(2, 4))
        self._label(frow, "文件", MUTED).pack(side="left")
        self.file_entry = tk.Entry(frow, textvariable=self.file_var, bg=CARD,
                                   fg=TEXT, insertbackground=TEXT, relief="flat",
                                   font=("Consolas", 8))
        self.file_entry.pack(side="left", fill="x", expand=True, padx=6, ipady=3)
        self.browse_btn = tk.Button(frow, text="…", command=self.browse_file,
                                    bg=CARD, fg=TEXT, relief="flat", bd=0,
                                    padx=7, pady=3, font=("Microsoft YaHei", 9),
                                    cursor="hand2")
        self.browse_btn.pack(side="left")

        brow = tk.Frame(b, bg=PANEL)
        brow.pack(fill="x", pady=(6, 0))
        self.connect_btn = self._btn(brow, "连接并开始记录", self.connect)
        self.connect_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.disconnect_btn = tk.Button(brow, text="断开", command=self.disconnect,
                                        bg=CARD, fg=TEXT, relief="flat", bd=0,
                                        padx=12, pady=5, font=("Microsoft YaHei", 9),
                                        cursor="hand2", state="disabled")
        self.disconnect_btn.pack(side="left")
        self.src_hint = self._label(b, "", MUTED, 8)
        self.src_hint.configure(wraplength=380, justify="left")
        self.src_hint.pack(fill="x", pady=(6, 0))

    def _build_stats_card(self):
        b = self._card("2 · 链路状态")
        self.stat_vars = {}
        rows = [("状态", "state"), ("包数", "packets"), ("样本", "samples"),
                ("速率", "rate"), ("丢包", "lost"), ("重同步", "resync"),
                ("时长", "elapsed"), ("标记", "markers")]
        grid = tk.Frame(b, bg=PANEL)
        grid.pack(fill="x")
        for i, (label, key) in enumerate(rows):
            tk.Label(grid, text=label, bg=PANEL, fg=MUTED,
                     font=("Microsoft YaHei", 9), anchor="w").grid(
                row=i, column=0, sticky="w", pady=1)
            v = tk.StringVar(value="—")
            self.stat_vars[key] = v
            tk.Label(grid, textvariable=v, bg=PANEL, fg=TEXT,
                     font=("Consolas", 9), anchor="e").grid(
                row=i, column=1, sticky="e", padx=(12, 0), pady=1)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        self.warn_label = tk.Label(b, text="", bg=PANEL, fg=WARN,
                                   font=("Microsoft YaHei", 8), anchor="w",
                                   justify="left", wraplength=330)
        self.warn_label.pack(fill="x", pady=(6, 2))
        self.selftest_btn = self._btn(b, "运行自检", self.self_test, bg=CARD, fg=TEXT)
        self.selftest_btn.pack(fill="x", pady=(4, 0))

    def _build_channel_card(self):
        b = self._card("3 · 通道质量（最近 2 s）")
        cols = ("ch", "rms", "status")
        self.tree = ttk.Treeview(b, columns=cols, show="headings", height=8)
        self.tree.heading("ch", text="通道")
        self.tree.heading("rms", text="RMS µV")
        self.tree.heading("status", text="状态")
        self.tree.column("ch", width=120, anchor="w")
        self.tree.column("rms", width=80, anchor="e")
        self.tree.column("status", width=70, anchor="center")
        self.tree.pack(fill="x")
        self.tree.tag_configure("ok", foreground=ACCENT)
        self.tree.tag_configure("warn", foreground=WARN)
        self.tree.tag_configure("bad", foreground=DANGER)
        self.tree.tag_configure("off", foreground=MUTED)
        for i in range(8):
            self.tree.insert("", "end", iid=str(i),
                             values=(f"CH{i + 1} {CH_NAMES[i]}", "—", "—"),
                             tags=("off",))

    def _build_record_card(self):
        b = self._card("4 · 录制")
        row = tk.Frame(b, bg=PANEL)
        row.pack(fill="x")
        self._label(row, "前缀", MUTED).pack(side="left")
        tk.Entry(row, textvariable=self.name_var, bg=CARD, fg=TEXT,
                 insertbackground=TEXT, relief="flat", font=("Consolas", 8),
                 width=20).pack(side="left", fill="x", expand=True, padx=6, ipady=3)
        tk.Checkbutton(b, text="同时写逐样本 CSV（体积大）", variable=self.save_csv_var,
                       bg=PANEL, fg=MUTED, selectcolor=CARD, activebackground=PANEL,
                       activeforeground=TEXT, font=("Microsoft YaHei", 8),
                       highlightthickness=0).pack(anchor="w", pady=(4, 0))
        self.rec_label = self._label(b, "串口模式连接后自动录制；断开时写 meta。", MUTED, 8)
        self.rec_label.configure(wraplength=380, justify="left")
        self.rec_label.pack(fill="x", pady=(4, 2))
        self._btn(b, "打开输出目录", self.open_output, bg=CARD, fg=TEXT).pack(
            fill="x", pady=(4, 0))

    def _build_mi_card(self):
        b = self._card("5 · 运动想象（2b 时序）")
        fields = [("每类 trial", "trials_per_class", 10, 1, 100),
                  ("组数", "blocks", 3, 1, 20),
                  ("注视 s", "fixation", 3.0, 0.5, 10.0),
                  ("cue s", "cue", 1.25, 0.2, 5.0),
                  ("想象 s", "imagery", 4.0, 1.0, 10.0),
                  ("休息 s", "rest", 4.0, 0.5, 20.0),
                  ("休息抖动 s", "rest_jitter", 1.0, 0.0, 5.0),
                  ("组间休息 s", "block_rest", 20.0, 0.0, 120.0)]
        grid = tk.Frame(b, bg=PANEL)
        grid.pack(fill="x")
        for i, (label, key, val, lo, hi) in enumerate(fields):
            r, c = divmod(i, 2)
            tk.Label(grid, text=label, bg=PANEL, fg=MUTED,
                     font=("Microsoft YaHei", 8), anchor="w").grid(
                row=r, column=2 * c, sticky="w", padx=(0 if c == 0 else 8, 2), pady=2)
            sp = ttk.Spinbox(grid, from_=lo, to=hi, increment=0.25 if isinstance(val, float) else 1,
                             textvariable=self.mi_vars[key], width=6,
                             style="Dark.TSpinbox")
            sp.grid(row=r, column=2 * c + 1, sticky="w", pady=2)
        tk.Checkbutton(b, text="提示屏使用窗口模式（不占满全屏）", variable=self.cue_windowed,
                       bg=PANEL, fg=MUTED, selectcolor=CARD, activebackground=PANEL,
                       activeforeground=TEXT, font=("Microsoft YaHei", 8),
                       highlightthickness=0).pack(anchor="w", pady=(6, 0))
        brow = tk.Frame(b, bg=PANEL)
        brow.pack(fill="x", pady=(6, 0))
        self.cue_btn = self._btn(brow, "打开提示屏并开始", self.start_cue)
        self.cue_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.cue_stop_btn = tk.Button(brow, text="中止", command=self.stop_cue,
                                      bg=CARD, fg=TEXT, relief="flat", bd=0,
                                      padx=12, pady=5, font=("Microsoft YaHei", 9),
                                      cursor="hand2", state="disabled")
        self.cue_stop_btn.pack(side="left")
        self._label(b, "提示屏里 Esc 也可中止；注视期必须保持 3 s。", MUTED, 8).pack(
            fill="x", pady=(6, 0))

    def _build_replay_card(self):
        b = self._card("6 · 回放 / 转发")
        row = tk.Frame(b, bg=PANEL)
        row.pack(fill="x")
        self._label(row, "倍速", MUTED).pack(side="left")
        tk.Scale(row, from_=0.5, to=2.0, resolution=0.1, orient="horizontal",
                 variable=self.speed_var, bg=PANEL, fg=TEXT, highlightthickness=0,
                 troughcolor=CARD, activebackground=ACCENT, length=170,
                 font=("Consolas", 8), showvalue=True).pack(side="left", padx=6)
        tk.Checkbutton(row, text="循环", variable=self.loop_var, bg=PANEL, fg=MUTED,
                       selectcolor=CARD, activebackground=PANEL, activeforeground=TEXT,
                       font=("Microsoft YaHei", 8), highlightthickness=0).pack(side="right")

        frow = tk.Frame(b, bg=PANEL)
        frow.pack(fill="x", pady=(4, 2))
        self._label(frow, "转发串口", MUTED).pack(side="left")
        self.forward_combo = ttk.Combobox(frow, textvariable=self.forward_var,
                                          width=12, state="readonly",
                                          style="Dark.TCombobox")
        self.forward_combo.pack(side="left", padx=6)
        tk.Checkbutton(frow, text="0xA0→0x41", variable=self.rewrite_var,
                       bg=PANEL, fg=MUTED, selectcolor=CARD, activebackground=PANEL,
                       activeforeground=TEXT, font=("Microsoft YaHei", 8),
                       highlightthickness=0).pack(side="right")
        warn = self._label(b, "转发给 Zynq 前先确认已做电气隔离；1.4× 以上会超 115200 带宽。",
                           WARN, 8)
        warn.configure(wraplength=380, justify="left")
        warn.pack(fill="x", pady=(2, 4))

        brow = tk.Frame(b, bg=PANEL)
        brow.pack(fill="x")
        self.pause_btn = tk.Button(brow, text="暂停", command=self.toggle_pause,
                                   bg=CARD, fg=TEXT, relief="flat", bd=0,
                                   padx=12, pady=5, font=("Microsoft YaHei", 9),
                                   cursor="hand2", state="disabled")
        self.pause_btn.pack(side="left")
        self.stop_btn = tk.Button(brow, text="停止回放", command=self.disconnect,
                                  bg=CARD, fg=TEXT, relief="flat", bd=0,
                                  padx=12, pady=5, font=("Microsoft YaHei", 9),
                                  cursor="hand2", state="disabled")
        self.stop_btn.pack(side="left", padx=(6, 0))

    def _build_wave_tab(self):
        tab = tk.Frame(self.nb, bg=PANEL)
        self.nb.add(tab, text="  时域波形  ")
        bar = tk.Frame(tab, bg=PANEL)
        bar.pack(fill="x", padx=8, pady=(4, 0))
        tk.Label(bar, text="量程 ±", bg=PANEL, fg=MUTED,
                 font=("Microsoft YaHei", 8)).pack(side="left")
        ttk.Spinbox(bar, from_=10, to=5000, increment=10, textvariable=self.range_var,
                    width=7, style="Dark.TSpinbox").pack(side="left", padx=4)
        tk.Label(bar, text="µV", bg=PANEL, fg=MUTED,
                 font=("Microsoft YaHei", 8)).pack(side="left")
        tk.Checkbutton(bar, text="自动量程", variable=self.auto_range_var, bg=PANEL,
                       fg=MUTED, selectcolor=CARD, activebackground=PANEL,
                       activeforeground=TEXT, font=("Microsoft YaHei", 8),
                       highlightthickness=0).pack(side="left", padx=10)
        tk.Checkbutton(bar, text="去直流", variable=self.dc_var, bg=PANEL,
                       fg=MUTED, selectcolor=CARD, activebackground=PANEL,
                       activeforeground=TEXT, font=("Microsoft YaHei", 8),
                       highlightthickness=0).pack(side="left")
        tk.Label(bar, text="CH1=C3  CH2=Cz  CH3=C4", bg=PANEL, fg=MUTED,
                 font=("Consolas", 8)).pack(side="right")

        self.fig = Figure(figsize=(8, 4.2), dpi=100, facecolor=BG)
        self.fig.subplots_adjust(left=0.07, right=0.995, top=0.99, bottom=0.07, hspace=0.06)
        self.wave_axes = []
        for i in range(8):
            ax = self.fig.add_subplot(8, 1, i + 1, facecolor=PANEL)
            ax.set_yticks([])
            ax.set_xticks([])
            for sp in ax.spines.values():
                sp.set_color(BORDER)
            ax.tick_params(colors=MUTED, labelsize=7)
            line, = ax.plot([], [], color=CH_COLORS[i], lw=0.85, antialiased=True)
            ax.text(0.006, 0.80, f"CH{i + 1} {CH_NAMES[i]}", transform=ax.transAxes,
                    color=CH_COLORS[i], fontsize=8, va="top")
            ax.set_xlim(-5, 0)
            ax.set_ylim(-150, 150)
            if i < 7:
                ax.tick_params(labelbottom=False)
            else:
                ax.set_xlabel("时间 (s)", color=MUTED, fontsize=8)
            self.wave_axes.append((ax, line))
        self.wave_canvas = FigureCanvasTkAgg(self.fig, master=tab)
        w = self.wave_canvas.get_tk_widget()
        w.configure(bg=BG, highlightthickness=0)
        w.pack(fill="both", expand=True, padx=4, pady=(2, 4))

    def _build_spec_tab(self):
        tab = tk.Frame(self.nb, bg=PANEL)
        self.nb.add(tab, text="  频谱 / 通道质量  ")
        self.spec_fig = Figure(figsize=(8, 5.0), dpi=100, facecolor=BG)
        self.spec_fig.subplots_adjust(left=0.11, right=0.98, top=0.97, bottom=0.14)
        ax = self.spec_fig.add_subplot(111, facecolor=PANEL)
        ax.set_xlim(1, 40)
        ax.set_xlabel("频率 (Hz)", color=MUTED, fontsize=9)
        ax.set_ylabel("PSD (µV²/Hz)", color=MUTED, fontsize=9)
        ax.set_yscale("log")
        ax.grid(True, color=BORDER, alpha=0.5, lw=0.6)
        ax.tick_params(colors=MUTED, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color(BORDER)
        self.spec_lines = []
        for ch in (0, 1, 2):
            line, = ax.plot([], [], color=CH_COLORS[ch], lw=1.2,
                            label=f"CH{ch + 1} {CH_NAMES[ch]}")
            self.spec_lines.append(line)
        ax.legend(facecolor=CARD, edgecolor=BORDER, labelcolor=TEXT,
                  fontsize=8, loc="upper right")
        self.spec_canvas = FigureCanvasTkAgg(self.spec_fig, master=tab)
        w = self.spec_canvas.get_tk_widget()
        w.configure(bg=BG, highlightthickness=0)
        w.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_log(self, right):
        frame = tk.Frame(right, bg=PANEL, highlightbackground=BORDER,
                         highlightthickness=1)
        frame.pack(side="bottom", fill="x", padx=18, pady=(6, 8))
        tk.Label(frame, text="运行日志", bg=PANEL, fg=MUTED,
                 font=("Microsoft YaHei", 9, "bold"), anchor="w").pack(
            fill="x", padx=12, pady=(6, 2))
        self.log_text = tk.Text(frame, height=4, bg="#0a1424", fg="#cbd5e1",
                                insertbackground=TEXT, relief="flat",
                                font=("Microsoft YaHei", 9), wrap="word",
                                padx=8, pady=4)
        self.log_text.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(0, 8))
        scr = tk.Scrollbar(frame, command=self.log_text.yview, bg=CARD,
                           troughcolor=PANEL, relief="flat", width=10)
        scr.pack(side="right", fill="y", padx=(0, 6), pady=(0, 8))
        self.log_text.configure(yscrollcommand=scr.set)

    # ── 小工具 ────────────────────────────────────────────
    def log(self, msg):
        ts = time.strftime("%H:%M:%S")
        try:
            self.log_text.insert("end", f"[{ts}] {msg}\n")
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > 400:
                self.log_text.delete("1.0", "80.0")
            self.log_text.see("end")
        except Exception:
            pass

    def _set_status(self, text, color=MUTED):
        try:
            self.status_pill.configure(text=text, fg=color)
        except Exception:
            pass

    def refresh_ports(self):
        ports = list_com_ports()
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])
        self.forward_combo["values"] = ["（不转发）"] + ports
        if self.forward_var.get() not in self.forward_combo["values"]:
            self.forward_var.set("（不转发）")

    def _mode_changed(self):
        serial = self.mode_var.get() == "serial"
        self.port_combo.configure(state="readonly" if serial else "disabled")
        self.refresh_btn.configure(state="normal" if serial else "disabled")
        self.file_entry.configure(state="disabled" if serial else "normal")
        self.browse_btn.configure(state="disabled" if serial else "normal")
        self.connect_btn.configure(text="连接并开始记录" if serial else "开始回放")
        self.forward_combo.configure(state="disabled" if serial else "readonly")
        if serial:
            self.src_hint.configure(text="串口模式会先发 s、v 复位，再发 b 推流；请先关闭 OpenBCI GUI。")
        else:
            self.src_hint.configure(text="文件模式按 250 Hz 回放；可循环、调速，或转发到 Zynq。")

    def browse_file(self):
        p = filedialog.askopenfilename(
            title="选择 .bin",
            initialdir=os.path.join(ROOT, "data", "raw"),
            filetypes=[("Cyton byte stream", "*.bin"), ("All files", "*.*")])
        if p:
            self.file_var.set(p)

    def start_demo(self):
        path = os.path.join(HERE, "sample_stream.bin")
        if not os.path.exists(path):
            self.log(f"找不到演示文件 {path}")
            return
        self.mode_var.set("file")
        self.file_var.set(path)
        self._mode_changed()
        self.log("演示模式：sample_stream.bin（3 s 循环）")
        self.connect()

    # ── 连接 ──────────────────────────────────────────────
    def connect(self):
        if self.source is not None:
            return
        self.samples.clear()
        self.spec_samples.clear()
        self.last_idx = -1
        self.last_marker = 0
        self.last_error_logged = None
        self.log_text.delete("1.0", "end") if hasattr(self, "log_text") else None

        if self.mode_var.get() == "serial":
            port = self.port_var.get().strip()
            if not port:
                messagebox.showwarning("没有串口", "先刷新并选择 Cyton Dongle 的 COM 口。")
                return
            name = safe_name(self.name_var.get().strip() or
                             ("gui_" + time.strftime("%Y%m%d_%H%M%S")))
            out = os.path.join(session_root(), "gui", time.strftime("%Y%m%d"))
            os.makedirs(out, exist_ok=True)
            acq = Acquisition(port=port, out_dir=out, name=name,
                              hw_marker=self.hw_marker_var.get(),
                              write_csv=self.save_csv_var.get())
            acq.add_listener(self._on_acq_sample)
            try:
                acq.open()
            except Exception as e:
                self.log(f"打开串口失败：{e}")
                messagebox.showerror("串口打开失败", str(e))
                return
            acq.start()
            self.source = acq
            self.kind = "serial"
            self.rec_dir = out
            self.rec_name = name
            self.rec_label.configure(text=f"录制到：{out}\\{name}_*")
            self.log(f"连接 {port}，录制前缀 {name}")
            self._set_status("● 等待数据", WARN)
            self._set_connected(True)
            self.root.after(200, self._poll_ready)
        else:
            path = self.file_var.get().strip()
            if not path or not os.path.exists(path):
                messagebox.showwarning("没有文件", "选择一个 .bin 文件。")
                return
            fwd = self.forward_var.get()
            fwd = None if fwd.startswith("（") else fwd
            try:
                src = ReplaySource(path, self._on_replay_sample,
                                   speed=self.speed_var.get(),
                                   loop=self.loop_var.get(),
                                   forward_port=fwd,
                                   rewrite_header=self.rewrite_var.get())
                src.start()
            except Exception as e:
                self.log(f"回放失败：{e}")
                messagebox.showerror("回放失败", str(e))
                return
            self.source = src
            self.kind = "replay"
            self.rec_dir = os.path.join(session_root(), "gui", time.strftime("%Y%m%d"))
            os.makedirs(self.rec_dir, exist_ok=True)
            self.rec_name = safe_name(os.path.splitext(os.path.basename(path))[0])
            self.rec_label.configure(text=f"回放：{os.path.basename(path)}"
                                         f"（{src.total} 包 / {src.total / FS:.1f} s）")
            self.log(f"回放 {os.path.basename(path)}：{src.total} 包，"
                     f"{src.total / FS:.1f} s，倍速 {self.speed_var.get():.1f}×")
            if src.forward_error:
                self.log(f"转发串口打开失败：{src.forward_error}")
            elif fwd:
                self.log(f"同时转发到 {fwd}（0xA0→0x41={self.rewrite_var.get()}）")
            self._set_status("● 回放中", BLUE)
            self._set_connected(True)

    def _set_connected(self, on):
        self.connect_btn.configure(state="disabled" if on else "normal")
        self.disconnect_btn.configure(state="normal" if on else "disabled")
        self.pause_btn.configure(state="normal" if (on and self.kind == "replay") else "disabled")
        self.stop_btn.configure(state="normal" if (on and self.kind == "replay") else "disabled")
        self.cue_btn.configure(state="normal" if on else "disabled")
        self.port_combo.configure(state="disabled" if on else "readonly")
        self.refresh_btn.configure(state="disabled" if on else "normal")
        self.file_entry.configure(state="disabled")
        self.browse_btn.configure(state="disabled")
        if not on:
            self._mode_changed()

    def _poll_ready(self):
        s = self.source
        if not isinstance(s, Acquisition):
            return
        if s.error is not None:
            self.log(f"采集线程错误：{s.error}")
            self.disconnect()
            return
        if s.ready.is_set():
            self.log("串口开始推流")
            self._set_status("● 采集中", ACCENT)
            return
        self.root.after(200, self._poll_ready)

    def _on_acq_sample(self, idx, seq, vals, marker, footer):
        self.q.put(("sample", int(idx), np.asarray(vals).copy(), int(marker), int(footer)))

    def _on_replay_sample(self, idx, seq, vals, marker, footer):
        self.q.put(("sample", int(idx), np.asarray(vals).copy(), int(marker), int(footer)))

    def disconnect(self):
        if self.cue is not None:
            try:
                self.cue.abort()
            except Exception:
                pass
            self.cue = None
        s = self.source
        self.source = None
        if s is not None:
            try:
                s.stop()
            except Exception:
                pass
            if isinstance(s, Acquisition):
                try:
                    meta = s.write_meta()
                    self.log(f"已保存 {s.bin_path}")
                    if meta.get("lost_pct", 0) > 1:
                        self.log(f"⚠ 丢包 {meta['lost_pct']:.2f}% > 1%，记录不可用")
                except Exception as e:
                    self.log(f"写 meta 失败：{e}")
        self._set_connected(False)
        self._set_status("● 未连接", MUTED)
        self.pause_btn.configure(text="暂停")
        self.cue_stop_btn.configure(state="disabled")
        for i in range(8):
            self.tree.item(str(i), values=(f"CH{i + 1} {CH_NAMES[i]}", "—", "—"),
                           tags=("off",))
        self.log("已断开")

    def toggle_pause(self):
        s = self.source
        if not isinstance(s, ReplaySource):
            return
        if s.pause_evt.is_set():
            s.resume()
            self.pause_btn.configure(text="暂停")
            self._set_status("● 回放中", BLUE)
        else:
            s.pause()
            self.pause_btn.configure(text="继续")
            self._set_status("● 已暂停", WARN)

    # ── 事件泵 ────────────────────────────────────────────
    def _pump(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "sample":
                    _, idx, vals, marker, footer = item
                    self.samples.append(vals)
                    self.spec_samples.append(vals)
                    self.last_idx = idx
                    if marker and marker != self.last_marker:
                        self.log(f"marker {marker} @ sample {idx}")
                    self.last_marker = marker
                elif item[0] == "log":
                    self.log(item[1])
        except queue.Empty:
            pass
        now = time.perf_counter()
        if now - self.last_stat_t > 0.5:
            self.last_stat_t = now
            self._update_stats()
        if now - self.last_tree_t > 1.0:
            self.last_tree_t = now
            self._update_channels()
        self.root.after(50, self._pump)

    def _source_stats(self):
        s = self.source
        if s is None:
            return {}
        if isinstance(s, Acquisition):
            base = s.t_first or s.t_start
            el = max(1e-3, time.perf_counter() - base) if base else 0.0
            n = max(1, s.abs_idx + 1)
            return {
                "packets": s.n_pkt, "samples": max(0, s.abs_idx + 1),
                "rate": (s.n_pkt / el) if el else 0.0,
                "lost": s.lost, "loss": 100.0 * s.lost / n,
                "resync": s.parser.n_resync, "elapsed": el,
                "markers": s.marker_count, "done": s.done.is_set(),
                "error": s.error, "paused": False, "forward_error": None,
                "forwarding": False,
            }
        return s.stats()

    def _update_stats(self):
        st = self._source_stats()
        if not st:
            for v in self.stat_vars.values():
                v.set("—")
            self.warn_label.configure(text="")
            return
        err = st.get("error")
        state = "错误" if err else ("已结束" if st.get("done") else
                                    ("暂停" if st.get("paused") else "运行中"))
        self.stat_vars["state"].set(state)
        self.stat_vars["packets"].set(f"{st.get('packets', 0):,}")
        self.stat_vars["samples"].set(f"{st.get('samples', 0):,}")
        self.stat_vars["rate"].set(f"{st.get('rate', 0):.1f} Hz")
        self.stat_vars["lost"].set(f"{st.get('lost', 0)} ({st.get('loss', 0):.2f}%)")
        self.stat_vars["resync"].set(str(st.get("resync", 0)))
        self.stat_vars["elapsed"].set(fmt_elapsed(st.get("elapsed", 0)))
        self.stat_vars["markers"].set(str(st.get("markers", 0)))

        warns = []
        if err:
            warns.append(f"错误：{err}")
        if st.get("loss", 0) > 1.0:
            warns.append("丢包 > 1%，这份记录按 OpenBCI 口径不可用")
        elif st.get("loss", 0) > 0:
            warns.append(f"有丢包 {st.get('loss', 0):.2f}%，检查 USB/线材")
        if st.get("forward_error"):
            warns.append(f"转发失败：{st['forward_error']}")
        if st.get("forwarding") and self.speed_var.get() > 1.3:
            warns.append("转发倍速 > 1.3×，115200 带宽可能不够")
        self.warn_label.configure(text="\n".join(warns))

        if err and self.last_error_logged != str(err):
            self.last_error_logged = str(err)
            self.log(f"错误：{err}")
            self._set_status("● 错误", DANGER)
        elif st.get("paused"):
            self._set_status("● 已暂停", WARN)
        elif st.get("done") and self.kind == "replay":
            self._set_status("● 回放结束", MUTED)
        elif self.kind == "serial":
            self._set_status("● 采集中" if st.get("packets", 0) else "● 等待数据",
                             ACCENT if st.get("packets", 0) else WARN)
        else:
            self._set_status("● 回放中", BLUE)

    def _update_channels(self):
        if len(self.samples) < 50:
            return
        arr = np.array(list(self.samples)[-int(2 * FS):], dtype=np.float32)
        for i in range(8):
            v = arr[:, i]
            rms = float(np.sqrt(np.mean(v * v)))
            std = float(np.std(v))
            if np.all(np.abs(v) < 1e-6):
                status, tag = "未接", "off"
            elif std < 0.3:
                status, tag = "FLAT", ("bad" if i < 3 else "off")
            elif float(np.max(np.abs(v))) > 5000:
                status, tag = "RAIL", "bad"
            else:
                status, tag = "OK", "ok"
            self.tree.item(str(i), values=(f"CH{i + 1} {CH_NAMES[i]}",
                                           f"{rms:.1f}", status), tags=(tag,))

    # ── 自检 ──────────────────────────────────────────────
    def self_test(self):
        if self.source is None:
            messagebox.showinfo("自检", "还没有连接数据源。")
            return
        st = self._source_stats()
        checks = []
        checks.append(("数据源", "OK" if st.get("packets", 0) > 0 else "无数据"))
        checks.append(("包率", f"{st.get('rate', 0):.1f} Hz" +
                       (" OK" if abs(st.get("rate", 0) - FS) < 5 else " 偏差大")))
        checks.append(("丢包", f"{st.get('loss', 0):.2f}%" +
                       (" OK" if st.get("loss", 0) <= 1 else " FAIL")))
        checks.append(("重同步", str(st.get("resync", 0))))
        if len(self.samples) >= 50:
            arr = np.array(list(self.samples)[-int(2 * FS):], dtype=np.float32)
            for i in range(3):
                std = float(np.std(arr[:, i]))
                checks.append((f"CH{i + 1} {CH_NAMES[i]} std",
                               f"{std:.1f} µV" + (" OK" if std > 0.3 else " FLAT")))
        else:
            checks.append(("波形", "样本不足"))
        text = "\n".join(f"{k}: {v}" for k, v in checks)
        self.log("自检：" + " | ".join(f"{k}={v}" for k, v in checks))
        messagebox.showinfo("自检结果", text)

    # ── MI ────────────────────────────────────────────────
    def _mi_params(self):
        try:
            p = {k: v.get() for k, v in self.mi_vars.items()}
            p["trials_per_class"] = int(p["trials_per_class"])
            p["blocks"] = int(p["blocks"])
            p["windowed"] = bool(self.cue_windowed.get())
            if p["trials_per_class"] < 1 or p["blocks"] < 1:
                raise ValueError
            return p
        except Exception:
            messagebox.showwarning("参数不对", "trial 数和组数必须是正整数，其余是正数。")
            return None

    def start_cue(self):
        if self.source is None:
            messagebox.showinfo("没有数据源", "先连接串口或开始回放，再打开提示屏。")
            return
        if isinstance(self.source, Acquisition) and not self.source.ready.is_set():
            messagebox.showinfo("还没出数", "串口还在握手/等数据，等状态变成“采集中”再开始。")
            return
        if self.cue is not None:
            return
        p = self._mi_params()
        if p is None:
            return
        out = self.rec_dir or os.path.join(session_root(), "gui", time.strftime("%Y%m%d"))
        os.makedirs(out, exist_ok=True)
        name = safe_name((self.rec_name or self.name_var.get() or "mi") + "_mi")
        try:
            self.cue = CueRunner(self.root, self.source, out, name, p,
                                 on_log=self.log, on_done=self._cue_done)
        except Exception as e:
            self.cue = None
            self.log(f"提示屏启动失败：{e}")
            messagebox.showerror("提示屏启动失败", str(e))
            return
        self.cue_btn.configure(state="disabled")
        self.cue_stop_btn.configure(state="normal")

    def _cue_done(self):
        self.cue = None
        try:
            self.cue_btn.configure(state="normal")
            self.cue_stop_btn.configure(state="disabled")
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def stop_cue(self):
        if self.cue is not None:
            self.cue.abort()

    # ── 绘图 ──────────────────────────────────────────────
    def _render_wave(self):
        try:
            if len(self.samples) >= 2:
                arr = np.array(self.samples, dtype=np.float32).T
                n = arr.shape[1]
                if self.dc_var.get():
                    arr = arr - np.mean(arr, axis=1, keepdims=True)
                x = np.linspace(-(n - 1) / FS, 0.0, n)
                if self.auto_range_var.get():
                    rng = max(5.0, float(np.percentile(np.abs(arr), 99)) * 1.2)
                    rng = min(rng, 5000.0)
                else:
                    rng = max(1.0, float(self.range_var.get()))
                for i, (ax, line) in enumerate(self.wave_axes):
                    line.set_data(x, arr[i])
                    ax.set_xlim(x[0], 0.0)
                    ax.set_ylim(-rng, rng)
                self.wave_canvas.draw_idle()
        except Exception:
            pass
        self.root.after(90, self._render_wave)

    def _render_spec(self):
        try:
            if len(self.spec_samples) >= int(1 * FS):
                arr = np.array(self.spec_samples, dtype=np.float32)
                for ch, line in zip((0, 1, 2), self.spec_lines):
                    f, pxx = welch(arr[:, ch], fs=FS, nperseg=min(500, len(arr)),
                                   detrend="constant")
                    line.set_data(f, np.maximum(pxx, 1e-12))
                ax = self.spec_lines[0].axes
                ys = np.concatenate([np.asarray(l.get_ydata()) for l in self.spec_lines])
                if ys.size:
                    lo = max(1e-4, float(np.min(ys)) * 0.5)
                    hi = max(lo * 10, float(np.max(ys)) * 2.0)
                    ax.set_ylim(lo, hi)
                self.spec_canvas.draw_idle()
        except Exception:
            pass
        self.root.after(1000, self._render_spec)

    # ── 截图 / 关闭 ───────────────────────────────────────
    def _screenshot(self, path):
        try:
            from PIL import ImageGrab
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.focus_force()
            self.root.update_idletasks()
            self.root.update()
            time.sleep(0.8)
            x = self.root.winfo_rootx()
            y = self.root.winfo_rooty()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            img.save(path)
            self.log(f"截图已保存 {path}")
        except Exception as e:
            self.log(f"截图失败：{e}")
        self.root.after(400, self.on_close)

    def open_output(self):
        if self.rec_dir and os.path.isdir(self.rec_dir):
            try:
                os.startfile(self.rec_dir)
            except Exception as e:
                self.log(f"打开目录失败：{e}")
        else:
            messagebox.showinfo("输出目录", "还没有录制目录；先连接一次。")

    def on_close(self):
        try:
            if self.cue is not None:
                self.cue.abort()
            self.disconnect()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="OpenBCI 测试台")
    ap.add_argument("--demo", action="store_true", help="载入 sim/host_tools/sample_stream.bin 循环演示")
    ap.add_argument("--screenshot", default=None, help="启动后截图并退出的路径（开发用）")
    ap.add_argument("--shot-delay", type=float, default=4.0, help="截图前等待秒数")
    a = ap.parse_args()
    root = tk.Tk()
    TestBench(root, demo=a.demo, screenshot=a.screenshot, shot_delay=a.shot_delay)
    root.mainloop()


if __name__ == "__main__":
    main()
