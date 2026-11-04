#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Cyton 串口模拟器 —— 图形界面版

在 cyton_sim.py 的基础上加一个简单 GUI：
  · 选串口（自动枚举，可刷新）
  · 选数据集路径（默认指向已下载的 BCI IV 2b），自动扫描可选被试/会话
  · 实时绘制正在发送的脑电波形

核心逻辑全部复用 cyton_sim.py，两边包格式保证一致。

用法:
  python cyton_sim_gui.py
"""
import os
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib import font_manager  # noqa: E402

# 中文字体
for _fp in (r"C:\WINDOWS\Fonts\msyh.ttc", r"C:\WINDOWS\Fonts\simhei.ttf"):
    if os.path.exists(_fp):
        font_manager.fontManager.addfont(_fp)
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cyton_sim import (FS, UV_PER_COUNT, FileSink, SerialSink, TcpSink,  # noqa: E402
                       build_packet, load_gdf, load_mat, make_8ch)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_GDF = os.path.join(ROOT, "data", "raw", "BCICIV_2b_gdf")
DEFAULT_MAT = os.path.join(ROOT, "matlab", "data")
CH_COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e",
             "#9467bd", "#8c564b", "#e377c2", "#7f7f7f"]
CH_LABELS = ["ch1", "ch2", "ch3", "ch4", "ch5", "ch6", "ch7", "ch8"]


# ═══════════════════════════════════════════════════════════
#  环形缓冲：发送线程写，GUI 线程读
# ═══════════════════════════════════════════════════════════
class RingBuffer:
    def __init__(self, n_ch=8, n=1250):
        self.buf = np.zeros((n_ch, n), dtype=np.float32)
        self.n = n
        self.idx = 0          # 下一个写入位置
        self.count = 0        # 累计写入的点数（单调递增）
        self.lock = threading.Lock()

    @property
    def filled(self):
        """缓冲区里当前有效的点数（未满时 < n）"""
        return min(self.count, self.n)

    def push(self, block):
        """block: (n_ch, k)"""
        k = block.shape[1]
        if k <= 0:
            return
        if k >= self.n:
            with self.lock:
                self.buf[:] = block[:, -self.n:]
                self.idx = 0
                self.count += k
            return
        with self.lock:
            end = self.idx + k
            if end <= self.n:
                self.buf[:, self.idx:end] = block
            else:
                cut = self.n - self.idx
                self.buf[:, self.idx:] = block[:, :cut]
                self.buf[:, :end - self.n] = block[:, cut:]
            self.idx = end % self.n
            self.count += k

    def snapshot(self):
        """返回 (已按时间顺序排好的数组, 有效点数)。

        未满时只返回尾部 filled 个点，避免把初始的零当成数据显示。
        """
        with self.lock:
            f = min(self.count, self.n)
            if self.count < self.n:
                return self.buf[:, :f].copy(), f
            return np.roll(self.buf, -self.idx, axis=1).copy(), f


# ═══════════════════════════════════════════════════════════
#  发送线程
# ═══════════════════════════════════════════════════════════
class Sender(threading.Thread):
    def __init__(self, cfg, ring, status_cb):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.ring = ring
        self.status = status_cb
        self.stop_evt = threading.Event()
        self.stats = dict(pkts=0, late=0, late_max=0.0, rate=0.0, buf=0,
                          state="初始化", err=None)

    def stop(self):
        self.stop_evt.set()

    def run(self):
        c = self.cfg
        try:
            data, names = (load_gdf(c["subject"], c["session"], c["channels"])
                           if c["source"] == "gdf" else load_mat(c["subject"], c["channels"]))
            stream = make_8ch(data, len(names), c["dc_offset"])
        except SystemExit as e:
            self.stats["err"] = str(e); self.stats["state"] = "数据加载失败"; return
        except Exception as e:
            self.stats["err"] = f"{type(e).__name__}: {e}"
            self.stats["state"] = "数据加载失败"; return

        self.stats["n_chan_real"] = len(names)
        self.stats["names"] = names
        self.stats["duration"] = stream.shape[1] / FS / c["speed"]

        # ── 打开输出 ──
        try:
            if c["sink_kind"] == "串口":
                sink = SerialSink(c["port"], c["baud"])
            elif c["sink_kind"] == "文件":
                sink = FileSink(c["file"])
            else:
                sink = TcpSink(c["tcp_port"], timeout=120)
        except Exception as e:
            self.stats["err"] = f"打开输出失败: {e}"; self.stats["state"] = "错误"; return

        header = 0x41 if c["header"] == "41" else 0xA0
        self.stats["state"] = "等待 'b' 命令" if c["wait_cmd"] else "发送中"

        # ── 握手 ──
        if c["wait_cmd"]:
            from cyton_sim import INIT_TEXT
            while not self.stop_evt.is_set():
                cmd = sink.recv_cmd()
                if cmd == "v":
                    sink.write(INIT_TEXT); sink.flush()
                elif cmd == "b":
                    break
                elif cmd is None:
                    time.sleep(0.005)
            if self.stop_evt.is_set():
                sink.close(); self.stats["state"] = "已停止"; return
            self.stats["state"] = "发送中"

        # ── 推流 ──
        period = 1.0 / (FS * c["speed"])
        seq = 0
        i = 0
        blk = np.zeros((8, 32), dtype=np.float32)
        blk_n = 0
        t_start = time.perf_counter()
        next_t = t_start
        t_wall0 = t_start

        while not self.stop_evt.is_set():
            pkt = build_packet(stream[:, i], seq, header, c["aux"],
                               (time.perf_counter() - t_wall0) * 1000.0)
            try:
                sink.write(pkt)
            except Exception as e:
                self.stats["err"] = f"发送失败: {e}"; break

            seq = (seq + 1) & 0xFF
            self.stats["pkts"] += 1

            # 攒块给波形图，减少锁竞争
            blk[:, blk_n] = stream[:, i]; blk_n += 1
            if blk_n >= blk.shape[1]:
                self.ring.push(blk[:, :blk_n]); blk_n = 0

            # 节拍
            next_t += period
            now = time.perf_counter()
            if now < next_t:
                slack = next_t - now
                if slack > 0.002:
                    time.sleep(slack - 0.001)
                while time.perf_counter() < next_t and not self.stop_evt.is_set():
                    pass
            else:
                lateness = now - next_t
                self.stats["late"] += 1
                self.stats["late_max"] = max(self.stats["late_max"], lateness)
                if lateness > period * 4:
                    next_t = now

            # 命令轮询
            cmd = sink.recv_cmd()
            if cmd == "s":
                break
            if cmd == "v":
                from cyton_sim import INIT_TEXT
                sink.write(INIT_TEXT); sink.flush()

            # 状态
            if self.stats["pkts"] % 25 == 0:
                el = now - t_start
                self.stats["rate"] = self.stats["pkts"] / el if el > 0 else 0
                try:
                    self.stats["buf"] = sink.out_waiting()
                except Exception:
                    self.stats["buf"] = 0

            i += 1
            if i >= stream.shape[1]:
                if c["loop"]:
                    i = 0
                else:
                    break

        if blk_n:
            self.ring.push(blk[:, :blk_n])
        try:
            sink.flush(); sink.close()
        except Exception:
            pass
        self.stats["state"] = "已完成" if not self.stop_evt.is_set() else "已停止"


# ═══════════════════════════════════════════════════════════
#  数据集扫描
# ═══════════════════════════════════════════════════════════
def scan_gdf(path):
    """返回 {被试: [会话,...]}"""
    out = {}
    if not os.path.isdir(path):
        return out
    for f in os.listdir(path):
        m = re.match(r"^B(\d{2})(\d{2}[TE])\.gdf$", f, re.I)
        if m:
            out.setdefault(int(m.group(1)), []).append(m.group(2))
    return {k: sorted(v) for k, v in sorted(out.items())}


def scan_mat(path):
    out = {}
    if not os.path.isdir(path):
        return out
    for f in os.listdir(path):
        m = re.match(r"^subj(\d{2})\.mat$", f, re.I)
        if m:
            out[int(m.group(1))] = ["(试次拼接)"]
    return dict(sorted(out.items()))


# ═══════════════════════════════════════════════════════════
#  主窗口
# ═══════════════════════════════════════════════════════════
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cyton 串口模拟器 —— BCI IV 2b → Zynq PS")
        self.minsize(900, 600)
        # 尺寸和位置必须一次设完：分两次调用会用自然尺寸覆盖掉指定的尺寸
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(1080, sw - 40)
        h = min(700, sh - 90)
        self.geometry(f"{w}x{h}+{max(0,(sw-w)//2)}+{max(0,(sh-h)//3)}")

        self.sender = None
        self.ring = RingBuffer(8, int(FS * 5))       # 5 秒窗口
        self.y_scale = tk.DoubleVar(value=30.0)      # µV/格
        self._build_ui()
        self.refresh_ports()
        self.on_source_change()

        self.after(40, self.tick_plot)
        self.after(200, self.tick_status)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ── 界面 ──
    def _build_ui(self):
        pad = dict(padx=6, pady=3)

        # ── 第一行：串口 ──
        f1 = ttk.LabelFrame(self, text="输出")
        f1.pack(fill="x", **pad)

        ttk.Label(f1, text="方式:").grid(row=0, column=0, sticky="e")
        self.sink_kind = ttk.Combobox(f1, values=["串口", "文件", "TCP"],
                                      width=6, state="readonly")
        self.sink_kind.set("串口")
        self.sink_kind.grid(row=0, column=1, sticky="w")
        self.sink_kind.bind("<<ComboboxSelected>>", lambda e: self._sink_ui())

        ttk.Label(f1, text="串口:").grid(row=0, column=2, sticky="e")
        self.port = ttk.Combobox(f1, width=22, state="readonly")
        self.port.grid(row=0, column=3, sticky="w")
        ttk.Button(f1, text="刷新", width=6,
                   command=self.refresh_ports).grid(row=0, column=4, padx=4)

        ttk.Label(f1, text="波特率:").grid(row=0, column=5, sticky="e")
        self.baud = ttk.Combobox(f1, values=["115200", "230400", "460800", "921600"],
                                 width=8, state="readonly")
        self.baud.set("115200")
        self.baud.grid(row=0, column=6, sticky="w")

        # 文件/TCP 的额外输入
        self.lbl_extra = ttk.Label(f1, text="文件:")
        self.lbl_extra.grid(row=1, column=0, sticky="e")
        self.extra = ttk.Entry(f1, width=44)
        self.extra.grid(row=1, column=1, columnspan=4, sticky="we")
        ttk.Button(f1, text="选择…", width=8,
                   command=self.pick_file).grid(row=1, column=5, padx=4)

        # ── 第二行：数据集 ──
        f2 = ttk.LabelFrame(self, text="数据集")
        f2.pack(fill="x", **pad)

        ttk.Label(f2, text="格式:").grid(row=0, column=0, sticky="e")
        self.source = ttk.Combobox(f2, values=["GDF (原始连续)", "MAT (试次拼接)"],
                                   width=15, state="readonly")
        self.source.set("GDF (原始连续)")
        self.source.grid(row=0, column=1, sticky="w")
        self.source.bind("<<ComboboxSelected>>", lambda e: self.on_source_change())

        ttk.Label(f2, text="路径:").grid(row=0, column=2, sticky="e")
        self.path = ttk.Entry(f2, width=52)
        self.path.grid(row=0, column=3, columnspan=2, sticky="we")
        ttk.Button(f2, text="浏览…", width=8,
                   command=self.pick_dir).grid(row=0, column=5, padx=4)
        ttk.Button(f2, text="重扫", width=6,
                   command=self.on_source_change).grid(row=0, column=6)

        ttk.Label(f2, text="被试:").grid(row=1, column=0, sticky="e")
        self.subject = ttk.Combobox(f2, width=8, state="readonly")
        self.subject.grid(row=1, column=1, sticky="w")
        self.subject.bind("<<ComboboxSelected>>", lambda e: self.on_subject_change())

        ttk.Label(f2, text="会话:").grid(row=1, column=2, sticky="e")
        self.session = ttk.Combobox(f2, width=10, state="readonly")
        self.session.grid(row=1, column=3, sticky="w")

        ttk.Label(f2, text="通道:").grid(row=1, column=4, sticky="e")
        self.channels = ttk.Entry(f2, width=18)
        self.channels.insert(0, "C3,Cz,C4")
        self.channels.grid(row=1, column=5, sticky="w")

        # ── 第三行：参数 ──
        f3 = ttk.LabelFrame(self, text="包参数")
        f3.pack(fill="x", **pad)

        ttk.Label(f3, text="帧头:").grid(row=0, column=0, sticky="e")
        self.header = tk.StringVar(value="41")
        ttk.Radiobutton(f3, text="0x41 (固件 BOP)", variable=self.header,
                        value="41").grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(f3, text="0xA0 (dongle)", variable=self.header,
                        value="a0").grid(row=0, column=2, sticky="w")

        ttk.Label(f3, text="AUX:").grid(row=0, column=3, sticky="e")
        self.aux = ttk.Combobox(f3, values=["zero", "aux", "time"],
                                width=8, state="readonly")
        self.aux.set("zero")
        self.aux.grid(row=0, column=4, sticky="w")

        ttk.Label(f3, text="倍速:").grid(row=0, column=5, sticky="e")
        self.speed = ttk.Spinbox(f3, from_=0.1, to=2.0, increment=0.1, width=6)
        self.speed.set("1.0")
        self.speed.grid(row=0, column=6, sticky="w")

        ttk.Label(f3, text="直流偏移 µV:").grid(row=0, column=7, sticky="e")
        self.dc = ttk.Spinbox(f3, from_=-500, to=500, increment=50, width=7)
        self.dc.set("0")
        self.dc.grid(row=0, column=8, sticky="w")

        self.loop = tk.BooleanVar(value=True)
        ttk.Checkbutton(f3, text="循环", variable=self.loop).grid(row=1, column=1, sticky="w")
        self.wait_cmd = tk.BooleanVar(value=False)
        ttk.Checkbutton(f3, text="等 'b' 命令再开始",
                        variable=self.wait_cmd).grid(row=1, column=2, columnspan=2, sticky="w")

        # 带宽提示
        self.bw_label = ttk.Label(f3, text="", foreground="#555")
        self.bw_label.grid(row=1, column=5, columnspan=4, sticky="w")
        for w in (self.speed, self.baud):
            w.bind("<KeyRelease>", lambda e: self.update_bw())
            w.bind("<<ComboboxSelected>>", lambda e: self.update_bw())
        self.update_bw()

        # ── 控制按钮 ──
        f4 = ttk.Frame(self)
        f4.pack(fill="x", **pad)
        self.btn_start = ttk.Button(f4, text="▶  开始发送", command=self.start)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(f4, text="■  停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)

        ttk.Label(f4, text="波形幅度 µV/格:").pack(side="left", padx=(20, 2))
        ttk.Spinbox(f4, from_=5, to=500, increment=5, width=7,
                    textvariable=self.y_scale).pack(side="left")

        self.run_state = ttk.Label(f4, text="就绪", foreground="#2E7D32",
                                   font=("Microsoft YaHei", 10, "bold"))
        self.run_state.pack(side="right", padx=10)

        # ── 波形图 ──
        f5 = ttk.LabelFrame(self, text="正在发送的波形（Zynq 将收到的内容）")
        f5.pack(fill="both", expand=True, **pad)

        self.fig = Figure(figsize=(10, 3.3), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("#111820")
        self.fig.subplots_adjust(left=0.075, right=0.995, top=0.96, bottom=0.12)
        self.lines = []
        for c in range(8):
            ln, = self.ax.plot([], [], lw=0.8, color=CH_COLORS[c])
            self.lines.append(ln)
        self.ax.set_xlim(-5, 0)
        self.ax.set_ylim(-1.1, 15.1)      # 给最上和最下两条留出摆幅余量，否则会被裁掉
        # ch1 画在顶部（offset = (7-c)*2），所以标签要反过来排
        self.ax.set_yticks(np.arange(8) * 2.0)
        self.ax.set_yticklabels(list(reversed(CH_LABELS)), fontsize=8)
        self.ax.set_xlabel("时间 (s)", fontsize=9)
        self.ax.grid(axis="x", alpha=0.25, color="#7f8c8d")
        self.ax.tick_params(labelsize=8)
        for sp in self.ax.spines.values():
            sp.set_color("#666")
        self.canvas = FigureCanvasTkAgg(self.fig, master=f5)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # ── 状态栏 ──
        self.status = ttk.Label(self, text="就绪", anchor="w",
                                relief="sunken", font=("Consolas", 9))
        self.status.pack(fill="x", side="bottom")

    # ── 辅助 ──
    def _sink_ui(self):
        k = self.sink_kind.get()
        if k == "串口":
            self.lbl_extra.config(text="串口:")
            self.extra.delete(0, "end")
            self.extra.config(state="disabled")
        elif k == "文件":
            self.lbl_extra.config(text="文件:")
            self.extra.config(state="normal")
            if not self.extra.get():
                self.extra.insert(0, os.path.join(HERE, "out_stream.bin"))
        else:
            self.lbl_extra.config(text="端口:")
            self.extra.config(state="normal")
            self.extra.delete(0, "end"); self.extra.insert(0, "9000")

    def pick_file(self):
        if self.sink_kind.get() == "文件":
            p = filedialog.asksaveasfilename(defaultextension=".bin",
                                             initialdir=HERE)
            if p:
                self.extra.delete(0, "end"); self.extra.insert(0, p)

    def refresh_ports(self):
        try:
            import serial.tools.list_ports as lp
            ps = [f"{p.device} — {p.description}" for p in lp.comports()]
        except Exception:
            ps = []
        self.port["values"] = ps
        if ps and not self.port.get():
            self.port.current(0)
        if not ps:
            self.port.set("(未发现串口)")

    def pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.path.get() or ROOT)
        if d:
            self.path.delete(0, "end"); self.path.insert(0, d)
            self.on_source_change()

    def on_source_change(self):
        is_gdf = self.source.get().startswith("GDF")
        if not self.path.get().strip() or (
                is_gdf and os.path.basename(self.path.get().rstrip("\\/")) != "BCICIV_2b_gdf"
                and not os.path.isdir(self.path.get())):
            self.path.delete(0, "end")
            self.path.insert(0, DEFAULT_GDF if is_gdf else DEFAULT_MAT)
        elif is_gdf and "matlab" in self.path.get():
            self.path.delete(0, "end"); self.path.insert(0, DEFAULT_GDF)
        elif not is_gdf and "BCICIV" in self.path.get():
            self.path.delete(0, "end"); self.path.insert(0, DEFAULT_MAT)

        idx = scan_gdf(self.path.get()) if is_gdf else scan_mat(self.path.get())
        self._index = idx
        subs = [f"B{k:02d}" for k in idx] if is_gdf else [f"subj{k:02d}" for k in idx]
        self.subject["values"] = subs
        if subs:
            self.subject.current(0)
            self.on_subject_change()
        else:
            self.session["values"] = []
            self.session.set("")
        self.update_bw()

    def on_subject_change(self):
        s = self.subject.get()
        if not s or not getattr(self, "_index", None):
            return
        k = int(re.sub(r"\D", "", s))
        self.session["values"] = self._index.get(k, [])
        if self._index.get(k):
            self.session.current(0)

    def update_bw(self):
        try:
            spd = float(self.speed.get()); baud = float(self.baud.get())
        except Exception:
            return
        use = 33 * FS * spd * 10 / baud
        col = "#2E7D32" if use < 0.8 else ("#E65100" if use < 0.95 else "#C62828")
        txt = (f"带宽占用 {use*100:.1f}%（33B × {FS*spd:.0f}Hz @ {baud:.0f}）")
        if use >= 0.95:
            txt += "  ⚠ 超限，会丢时序"
        self.bw_label.config(text=txt, foreground=col)

    # ── 启停 ──
    def collect_cfg(self):
        k = self.sink_kind.get()
        s = self.subject.get()
        return dict(
            sink_kind=k,
            port=(self.port.get().split("—")[0].strip() if k == "串口" else ""),
            baud=int(self.baud.get()),
            file=(self.extra.get().strip() if k == "文件" else ""),
            tcp_port=(int(self.extra.get() or 9000) if k == "TCP" else 9000),
            source=("gdf" if self.source.get().startswith("GDF") else "mat"),
            subject=int(re.sub(r"\D", "", s)) if s else 1,
            session=(self.session.get() or "01T"),
            channels=[c.strip() for c in self.channels.get().split(",") if c.strip()],
            header=self.header.get(), aux=self.aux.get(),
            speed=float(self.speed.get()), dc_offset=float(self.dc.get()),
            loop=self.loop.get(), wait_cmd=self.wait_cmd.get(),
        )

    def start(self):
        if self.sender and self.sender.is_alive():
            return
        cfg = self.collect_cfg()
        if cfg["sink_kind"] == "串口" and not cfg["port"]:
            messagebox.showwarning("提示", "请先选择串口"); return
        if cfg["sink_kind"] == "文件" and not cfg["file"]:
            messagebox.showwarning("提示", "请先选择输出文件"); return

        self.ring = RingBuffer(8, int(FS * 5))
        for ln in self.lines:
            ln.set_data([], [])
        self.sender = Sender(cfg, self.ring, lambda: None)
        self.sender.start()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.run_state.config(text="启动中…", foreground="#1565C0")

    def stop(self):
        if self.sender:
            self.sender.stop()
        self.btn_stop.config(state="disabled")

    # ── 定时刷新 ──
    def tick_plot(self):
        try:
            buf, cnt = self.ring.snapshot()
        except Exception:
            buf, cnt = None, 0
        if buf is not None and cnt:
            n = buf.shape[1]
            t = (np.arange(n) - (n - 1)) / FS
            sc = float(self.y_scale.get())
            for c in range(8):
                off = (7 - c) * 2.0
                self.lines[c].set_data(t, off + np.clip(buf[c] / sc, -0.95, 0.95))
            span = max(n / FS, 0.5)
            self.ax.set_xlim(-span, 0)
            self.canvas.draw_idle()
        self.after(40, self.tick_plot)

    def tick_status(self):
        if self.sender:
            st = self.sender.stats
            name = " · ".join(st.get("names", [])) or "-"
            msg = (f"已发 {st['pkts']:>7d} 包 | {st['rate']:7.2f} Hz | "
                   f"迟到 {st['late']} 次(最大 {st['late_max']*1000:.1f} ms) | "
                   f"缓冲 {st['buf']} B | 真实通道 {name}")
            if st.get("err"):
                msg = "错误: " + st["err"]
                self.run_state.config(text="错误", foreground="#C62828")
            else:
                self.run_state.config(text=st["state"], foreground="#1565C0"
                                      if st["state"] == "发送中" else "#2E7D32")
            self.status.config(text=msg)
            if not self.sender.is_alive():
                self.btn_start.config(state="normal")
                self.btn_stop.config(state="disabled")
        self.after(200, self.tick_status)

    def on_close(self):
        if self.sender:
            self.sender.stop()
            time.sleep(0.2)
        self.destroy()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Cyton 模拟器 GUI")
    ap.add_argument("--list", action="store_true", help="列出串口后退出")
    ap.add_argument("--port", default=None, help="预选串口，如 COM3")
    ap.add_argument("--sink", default=None,
                    help="预选输出：串口号 | file:路径 | tcp:端口")
    ap.add_argument("--source", choices=["gdf", "mat"], default=None)
    ap.add_argument("--subject", type=int, default=None)
    ap.add_argument("--session", default=None)
    ap.add_argument("--autostart", action="store_true", help="启动后立即开始发送")
    args = ap.parse_args()

    if args.list:
        import serial.tools.list_ports as lp
        for p in lp.comports():
            print(f"  {p.device:8s} {p.description}")
        sys.exit(0)

    app = App()

    # ── 预填配置（便于复现场景 / 自动化测试）──
    spec = args.sink or args.port
    if spec:
        if spec.startswith("file:"):
            app.sink_kind.set("文件"); app._sink_ui(); app.extra.delete(0, "end")
            app.extra.insert(0, spec[5:])
        elif spec.startswith("tcp:"):
            app.sink_kind.set("TCP"); app._sink_ui(); app.extra.delete(0, "end")
            app.extra.insert(0, spec[4:])
        else:
            for i, v in enumerate(app.port["values"]):
                if v.startswith(spec):
                    app.port.current(i); break
    if args.source:
        app.source.set("GDF (原始连续)" if args.source == "gdf" else "MAT (试次拼接)")
        app.path.delete(0, "end")
        app.path.insert(0, DEFAULT_GDF if args.source == "gdf" else DEFAULT_MAT)
        app.on_source_change()
    if args.subject:
        for i, v in enumerate(app.subject["values"]):
            if v.endswith(f"{args.subject:02d}"):
                app.subject.current(i); app.on_subject_change(); break
    if args.session and args.session in list(app.session["values"]):
        app.session.set(args.session)
    if args.autostart:
        app.after(300, app.start)

    app.mainloop()
