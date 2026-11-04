#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OpenBCI Cyton 实采记录器（含左右手运动想象范式）

用途
────
1. 从 Cyton USB Dongle 的串口抓原始 33 字节包，存成 .bin（可直接给
   cyton_parse.py / cyton_sim.py --source bin 用）；
2. 同时导出逐样本 CSV（计数值 + µV），方便用 Excel / pandas 看；
3. 可选跑一个左右手运动想象（MI）范式：屏幕提示 + 事件表，事件按
   “第几个采样点”记录，不依赖主机墙钟；
4. 可选把 cue 写进 Cyton 的 AUX1（硬件 marker，4 ms 分辨率）。

为什么不用 OpenBCI GUI 录
──────────────────────────
GUI 录的是它自己的 .txt 格式，列定义/丢包口径都和本项目不一致。
本脚本直接保存 33 字节原始包，和 sim/host_tools/sample_stream.bin 同构，
cyton_parse.py 与 Zynq 端 pkt_parser 可以逐字节对拍。

快速开始
────────
  # 0) 看串口
  python openbci_record.py --list

  # 1) 先空采 30 s，确认能出数、丢包为 0
  python openbci_record.py --port COM3 --duration 30 --name subj01_test

  # 2) 正式采：3 组 × 每组 20 trial（左右各 10），约 12 分钟（含组间休息）
  python openbci_record.py --port COM3 --paradigm mi2 \
      --trials-per-class 10 --blocks 3 --name subj01_mi

  # 3) 只要静息/噪声段，采 2 分钟
  python openbci_record.py --port COM3 --duration 120 --name subj01_rest

  # 4) 不进硬件，把已有 .bin 转成 CSV（自测/补转）
  python openbci_record.py --input file:old.bin --out tmp --name old

输出（<out>/<name>_*）
──────────────────────
  <name>_raw.bin    33 B/包原始流（帧头 0xA0 或 0x41，原样保存）
  <name>_raw.csv    逐样本：计数、µV、帧尾、marker
  <name>_events.csv 长表事件：trial_start / cue / imagery_end / block_*
  <name>_meta.json  采样率、包数、丢包、串口、阻抗备注等

安全
────
Cyton 用电池供电时被试是浮地的。**采数时笔记本尽量用电池**；
若之后要把 Cyton 接到 Zynq（USB 供电），必须按 规划/04 号文档做
数字隔离，否则会在被试身上建立到市电地的通路。

协议
────
Cyton 250 Hz，8 通道，24 bit 大端，33 字节/包，115200 8N1。
命令：'v' 复位/版本，'b' 开始，'s' 停止；'/4' 进 marker 模式；
'\x60' + 1 字节 把 marker 写进下一包的 AUX1（固件 processInsertMarker）。
"""
import argparse
import csv
import datetime
import json
import os
import random
import sys
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np

from cyton_parse import CytonParser, decode, UV_PER_COUNT

FS = 250.0
PACKET_LEN = 33
CH_NAMES = ["C3", "Cz", "C4", "FC3", "FC4", "CP3", "CP4", "Pz"]
CLASS_NAME = {0: "left", 1: "right"}
MARK = {"left": 1, "right": 2, "trial": 3, "block": 4}

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def iso_now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sanitize(name):
    bad = '<>:"/\\|?* '
    s = "".join("_" if c in bad else c for c in str(name)).strip("_")
    return s or "rec"


def list_ports():
    try:
        from serial.tools import list_ports as lp
    except Exception as e:
        print(f"pyserial 不可用：{e}")
        return
    ports = sorted(lp.comports(), key=lambda p: p.device)
    if not ports:
        print("没有发现串口。检查：Dongle 是否插好、FTDI 驱动是否安装、GUI 是否占着端口。")
        return
    print(f"发现 {len(ports)} 个串口：")
    for p in ports:
        vidpid = ""
        if p.vid is not None and p.pid is not None:
            vidpid = f"  VID:PID={p.vid:04X}:{p.pid:04X}"
        print(f"  {p.device:8s} {p.description}{vidpid}")
    print("\nCyton Dongle 通常是 FTDI FT231X（VID:PID=0403:6015），"
          "Windows 显示为 USB Serial Port (COMx)。")


class Acquisition:
    """串口/文件 → 33 字节包 → .bin + .csv + meta。"""

    def __init__(self, port=None, infile=None, out_dir=".", name="rec",
                 hw_marker=False, write_csv=True):
        self.port = port
        self.infile = infile
        self.out_dir = out_dir
        self.name = name
        self.hw_marker = hw_marker
        self.write_csv = write_csv

        self.parser = CytonParser(None)       # 同时接受 0x41 / 0xA0
        self.listeners = []                   # GUI/测试台用：每包回调
        self.ser = None
        self.f = None
        self.bin_f = None
        self.csv_f = None
        self.csv_w = None

        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.ready = threading.Event()
        self.done = threading.Event()
        self.error = None
        self._closed = False

        self.n_pkt = 0
        self.abs_idx = -1
        self.prev_seq = None
        self.lost = 0
        self.jumps = 0
        self.first_header = None
        self.marker_mode_ok = False
        self.marker_count = 0
        self.last_marker = 0

        self.last_idx = -1
        self.last_host = 0.0
        self.t_start = 0.0
        self.t_first = 0.0
        self.t_end = 0.0
        self._last_flush = 0.0

        os.makedirs(out_dir, exist_ok=True)
        self.bin_path = os.path.join(out_dir, f"{name}_raw.bin")
        self.csv_path = os.path.join(out_dir, f"{name}_raw.csv") if write_csv else None
        self.meta_path = os.path.join(out_dir, f"{name}_meta.json")

    # ── 打开 ──────────────────────────────────────────────
    def open(self):
        self.bin_f = open(self.bin_path, "wb", buffering=1024 * 1024)
        if self.write_csv:
            self.csv_f = open(self.csv_path, "w", newline="", encoding="utf-8-sig")
            self.csv_w = csv.writer(self.csv_f)
            self.csv_w.writerow(
                ["sample_index", "seq"]
                + [f"ch{i+1}_count" for i in range(8)]
                + [f"ch{i+1}_uV" for i in range(8)]
                + ["footer", "marker"]
            )

        if self.infile:
            self.f = open(self.infile, "rb")
            if self.hw_marker:
                # 离线转换时，默认这份 .bin 是 marker 模式采的
                self.marker_mode_ok = True
            return

        try:
            import serial
        except Exception as e:
            raise RuntimeError(f"pyserial 未安装：{e}") from e
        try:
            self.ser = serial.Serial(self.port, 115200, timeout=0.05,
                                     write_timeout=1.0)
        except Exception as e:
            self._close()
            raise RuntimeError(
                f"打不开 {self.port}：{e}\n"
                "常见原因：OpenBCI GUI 还占着串口 / 端口号选错 / 没装 FTDI 驱动。"
            ) from e

    def add_listener(self, fn):
        """每收到一个合法包调用 fn(abs_idx, seq, vals, marker, footer)。"""
        self.listeners.append(fn)

    # ── 线程 ──────────────────────────────────────────────
    def start(self):
        self.t_start = time.perf_counter()
        self._last_flush = self.t_start
        self.thread = threading.Thread(target=self._run, name="acq", daemon=True)
        self.thread.start()

    def _run(self):
        try:
            if self.ser is not None:
                self._handshake()
            self.ready.set()
            while not self.stop_evt.is_set():
                chunk = self._read_chunk()
                if chunk is None:          # 文件读完
                    break
                if not chunk:
                    continue
                for pkt in self.parser.feed(chunk):
                    self._on_packet(pkt)
        except Exception as e:
            self.error = e
            self.ready.set()
        finally:
            self.done.set()
            self._close()

    def _read_chunk(self):
        if self.ser is not None:
            n = self.ser.in_waiting
            return self.ser.read(n if n else 1)
        b = self.f.read(4096)
        return None if not b else b

    def _read_until(self, token, timeout):
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            b = self.ser.read(1)
            if b:
                buf += b
                if buf.endswith(token):
                    break
        return buf

    def _handshake(self):
        s = self.ser
        s.reset_input_buffer()
        s.write(b"s")
        time.sleep(0.15)
        s.reset_input_buffer()
        s.write(b"v")
        self._read_until(b"$$$", 2.0)
        s.reset_input_buffer()

        if self.hw_marker:
            s.write(b"/4")
            resp = self._read_until(b"$$$", 1.0)
            self.marker_mode_ok = b"marker" in resp.lower()
            if not self.marker_mode_ok:
                print("  ⚠ 没能进入 marker 模式（固件可能不支持 /4）；"
                      "改用软件事件表对齐。")
            s.reset_input_buffer()

        # 丢掉 'v' 初始化串留下的 ASCII（避免被当成包/增加 resync 计数）
        self.parser = CytonParser(None)
        s.write(b"b")
        time.sleep(0.15)

    def _on_packet(self, pkt):
        vals, seq, footer, aux, _ = decode(pkt)

        if self.first_header is None:
            self.first_header = pkt[0]

        if self.prev_seq is None:
            self.abs_idx = 0
        else:
            d = (seq - self.prev_seq) & 0xFF
            if d == 0:
                d = 1
            self.abs_idx += d
            if d != 1:
                self.lost += d - 1
                self.jumps += 1
        self.prev_seq = seq

        marker = 0
        if self.marker_mode_ok:
            marker = (int(aux[0]) << 8) | int(aux[1])
            if marker != self.last_marker:
                self.marker_count += 1
                self.last_marker = marker

        self.bin_f.write(pkt)
        if self.csv_w is not None:
            self.csv_w.writerow(
                [self.abs_idx, seq]
                + [int(v) for v in vals]
                + [f"{float(v) * UV_PER_COUNT:.4f}" for v in vals]
                + [f"0x{footer:02X}", marker]
            )

        if self.n_pkt == 0:
            self.t_first = time.perf_counter()
        self.n_pkt += 1
        now = time.perf_counter()
        with self.lock:
            self.last_idx = self.abs_idx
            self.last_host = now
        for fn in list(self.listeners):
            try:
                fn(self.abs_idx, seq, vals, marker, footer)
            except Exception:
                pass
        if now - self._last_flush > 1.0:
            self._flush()
            self._last_flush = now

    def _flush(self):
        try:
            if self.bin_f and not self.bin_f.closed:
                self.bin_f.flush()
            if self.csv_f and not self.csv_f.closed:
                self.csv_f.flush()
        except Exception:
            pass

    # ── 事件锚点 ──────────────────────────────────────────
    def estimate_sample(self):
        """返回 (当前最可能对应的绝对采样序号, 最后一个已收样本序号)。

        串口是分块到达的（FTDI 通常 16 ms 一块），所以用“最后一个包的时间 +
        至今经过的时间”外推，最多外推 12 个样本（48 ms），防止读线程卡住时飞掉。
        2.5 s 的分析窗里 ±20 ms 完全可以接受；要更准就开 --hw-marker。
        """
        with self.lock:
            if self.last_idx < 0:
                return None, None
            li, lh = self.last_idx, self.last_host
        dt = max(0.0, time.perf_counter() - lh)
        dt = min(dt, 0.048)
        return li + int(round(dt * FS)), li

    def send_marker(self, value):
        if not (self.hw_marker and self.marker_mode_ok and self.ser is not None):
            return False
        try:
            with self.lock:
                self.ser.write(b"\x60" + bytes([int(value) & 0xFF]))
                self.ser.flush()
            return True
        except Exception:
            return False

    # ── 收尾 ──────────────────────────────────────────────
    def stop(self):
        self.stop_evt.set()
        if self.ser is not None:
            try:
                self.ser.write(b"s")
                if self.marker_mode_ok:
                    self.ser.write(b"/0")   # 退出 marker 模式，恢复默认
                self.ser.flush()
            except Exception:
                pass
        t = getattr(self, "thread", None)
        if t is not None:
            t.join(timeout=2.0)
        self._close()

    def _close(self):
        with self.lock:
            if self._closed:
                return
            self._closed = True
            self.t_end = time.perf_counter()
            for f in (self.bin_f, self.csv_f, self.f):
                try:
                    if f and not f.closed:
                        f.flush()
                        f.close()
                except Exception:
                    pass
            if self.ser is not None:
                try:
                    self.ser.close()
                except Exception:
                    pass

    def wait_ready(self, timeout=5.0):
        return self.ready.wait(timeout)

    def status_line(self):
        el = max(1e-3, time.perf_counter() - (self.t_first or self.t_start))
        n = max(1, self.abs_idx + 1)
        return (f"{self.n_pkt:6d} 包 / {n:6d} 样本 / {el:6.1f} s / "
                f"{self.n_pkt / el:6.1f} Hz / 丢 {self.lost} ({100.0*self.lost/n:.2f}%)")

    def write_meta(self):
        n = max(0, self.abs_idx + 1)
        el = max(1e-6, (self.t_end or time.perf_counter()) - self.t_start)
        meta = {
            "created": iso_now(),
            "source": ("serial:" + str(self.port)) if self.ser is not None
                      else ("file:" + str(self.infile)),
            "baud": 115200,
            "fs": FS,
            "n_channels": 8,
            "channels": CH_NAMES,
            "uv_per_count": UV_PER_COUNT,
            "n_packets": self.n_pkt,
            "n_samples": n,
            "duration_s": round(n / FS, 3),
            "wall_s": round(el, 3),
            "lost_samples": self.lost,
            "lost_pct": round(100.0 * self.lost / max(n, 1), 4),
            "seq_jumps": self.jumps,
            "parser_resync": self.parser.n_resync,
            "parser_bytes": self.parser.n_byte,
            "header": (f"0x{self.first_header:02X}" if self.first_header is not None else None),
            "hw_marker": bool(self.hw_marker),
            "marker_mode_ok": bool(self.marker_mode_ok),
            "marker_count": self.marker_count,
            "files": {
                "raw_bin": os.path.basename(self.bin_path),
                "raw_csv": os.path.basename(self.csv_path) if self.csv_path else None,
                "events_csv": (os.path.basename(os.path.join(self.out_dir, f"{self.name}_events.csv"))
                               if os.path.exists(os.path.join(self.out_dir, f"{self.name}_events.csv")) else None),
                "meta": os.path.basename(self.meta_path),
            },
            "note": "CH1..CH8; 项目 3 通道模型使用 CH1=C3, CH2=Cz, CH3=C4。",
        }
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return meta


class EventLog:
    """长表事件：一行一个事件，trial 号相同即可还原一个 trial。"""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "w", newline="", encoding="utf-8-sig")
        self.w = csv.writer(self.f)
        self.w.writerow(["trial", "block", "label", "class", "event",
                         "host_time", "sample", "marker_sent"])

    def add(self, trial="", block="", label="", cls="", event="",
            sample=None, marker_sent=0):
        self.w.writerow([trial, block, label, cls, event,
                         f"{time.time():.6f}",
                         "" if sample is None else int(sample),
                         int(bool(marker_sent))])
        self.f.flush()

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


def build_mi_trials(trials_per_class, blocks):
    """生成左右 1:1、块内不打乱到 3 连的 trial 列表。"""
    trials = []
    for b in range(blocks):
        labels = [0] * trials_per_class + [1] * trials_per_class
        random.shuffle(labels)
        for i in range(2, len(labels)):
            if labels[i] == labels[i - 1] == labels[i - 2]:
                for j in range(i + 1, len(labels)):
                    if labels[j] != labels[i]:
                        labels[i], labels[j] = labels[j], labels[i]
                        break
        for lab in labels:
            trials.append({"idx": len(trials), "block": b, "label": lab})
    return trials


def build_mi_phases(trials, blocks, fixation=3.0, cue=1.25, imagery=4.0,
                    rest=4.0, rest_jitter=1.0, block_rest=20.0):
    """(phase, duration, trial, block) 序列；phase ∈ block_start/trial_start/cue/imagery/rest/block_end。"""
    ph = []
    for b in range(blocks):
        ph.append(("block_start", 3.0, None, b))
        for t in [x for x in trials if x["block"] == b]:
            ph.append(("trial_start", fixation, t, b))
            ph.append(("cue", cue, t, b))
            ph.append(("imagery", max(0.0, imagery - cue), t, b))
            r = rest + random.uniform(-rest_jitter, rest_jitter)
            ph.append(("rest", max(1.0, r), t, b))
        ph.append(("block_end", block_rest, None, b))
    return ph


class MIParadigm:
    """3 s 注视 + cue + 4 s 想象 + 4 s 休息，时序与 BCI IV 2b 对齐。

    2b 的 768（trial 起点）到 769/770（cue）正好 3.0 s；
    项目模型窗 [3.5, 6.0] s 就是 cue 后 0.5–3.0 s。
    所以这里必须保留 3 s 的注视期，否则模型窗对不上。
    """

    def __init__(self, acq, out_dir, name, trials_per_class=10, blocks=3,
                 fixation=3.0, cue=1.25, imagery=4.0, rest=4.0,
                 rest_jitter=1.0, block_rest=20.0, windowed=False,
                 auto_start=False):
        self.acq = acq
        self.out_dir = out_dir
        self.name = name
        self.trials_per_class = trials_per_class
        self.blocks = blocks
        self.fixation = fixation
        self.cue = cue
        self.imagery = imagery
        self.rest = rest
        self.rest_jitter = rest_jitter
        self.block_rest = block_rest
        self.windowed = windowed
        self.auto_start = auto_start

        self.trials = self._build_trials()
        self.phases = self._build_phases()
        self.events = EventLog(os.path.join(out_dir, f"{name}_events.csv"))
        self.idx = 0
        self.started = False
        self.aborted = False
        self.cur = None
        self.root = None
        self.tk = None

    def _build_trials(self):
        return build_mi_trials(self.trials_per_class, self.blocks)

    def _build_phases(self):
        return build_mi_phases(self.trials, self.blocks, self.fixation, self.cue,
                               self.imagery, self.rest, self.rest_jitter,
                               self.block_rest)

    # ── Tk ────────────────────────────────────────────────
    def run(self):
        import tkinter as tk
        self.tk = tk
        root = tk.Tk()
        self.root = root
        root.title("MI 2-class")
        root.configure(bg="black")
        if self.windowed:
            root.geometry("1100x720")
        else:
            root.attributes("-fullscreen", True)
        self.canvas = tk.Canvas(root, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        root.update_idletasks()
        root.bind("<Escape>", lambda e: self._abort())
        root.protocol("WM_DELETE_WINDOW", self._abort)
        self._intro()
        if self.auto_start:
            root.after(300, self._on_space)
        root.mainloop()

    def close(self):
        self.events.close()

    def _intro(self):
        self.canvas.delete("all")
        txt = ("左右手运动想象\n\n"
               "1. 盯住屏幕中央的十字，身体放松，不要动。\n"
               "2. 出现箭头时，想象箭头所指那只手在张开、握紧。\n"
               "3. 想象“动的感觉”，不要真的动，也不要看自己的手。\n"
               "4. 头、眼睛、下巴保持不动，自然眨眼。\n\n"
               "按 空格 开始    Esc 中止")
        self._text(txt, int(min(self.canvas.winfo_screenwidth(),
                                self.canvas.winfo_screenheight()) * 0.026))
        self.root.bind("<space>", self._on_space)

    def _on_space(self, _evt=None):
        if self.started:
            return
        self.started = True
        self.root.unbind("<space>")
        self._next()

    def _next(self):
        if self.aborted:
            return
        if self.acq.error is not None:
            self._show_error(f"采集出错：{self.acq.error}")
            return
        if self.idx >= len(self.phases):
            self._finish()
            return
        phase, dur, trial, block = self.phases[self.idx]
        self.idx += 1
        self._on_phase(phase, trial, block)
        self.root.after(max(1, int(round(dur * 1000.0))), self._next)

    def _on_phase(self, phase, trial, block):
        if phase == "block_start":
            self._clear()
            self._text(f"第 {block + 1} / {self.blocks} 组\n\n准备…", 34)
            self._event("block_start", self.acq.estimate_sample()[0],
                        MARK["block"], block=block)
        elif phase == "trial_start":
            self.cur = dict(trial)
            sample, _ = self.acq.estimate_sample()
            self._clear()
            self._cross()
            self._event("trial_start", sample, MARK["trial"], block=block)
        elif phase == "cue":
            sample, _ = self.acq.estimate_sample()
            self._clear()
            self._arrow(trial["label"])
            self._event("cue", sample, MARK["left"] if trial["label"] == 0
                        else MARK["right"], block=block)
        elif phase == "imagery":
            self._clear()
            self._cross()
        elif phase == "rest":
            sample, _ = self.acq.estimate_sample()
            self._event("imagery_end", sample, 0, block=block)
            self.cur = None
            self._clear()
        elif phase == "block_end":
            self._clear()
            self._text(f"本组结束\n\n休息 {int(round(self.block_rest))} 秒", 34)
            self._event("block_end", self.acq.estimate_sample()[0],
                        MARK["block"], block=block)

    def _event(self, name, sample, marker, block=None):
        t = self.cur or {}
        label = t.get("label", "")
        cls = CLASS_NAME.get(label, "") if label != "" else ""
        sent = self.acq.send_marker(marker) if marker else False
        self.events.add(t.get("idx", ""), t.get("block", "" if block is None else block),
                        label, cls, name, sample, sent)

    # ── 画面 ──────────────────────────────────────────────
    def _clear(self):
        self.canvas.delete("all")

    def _text(self, s, size=28, color="#e5e7eb"):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 100 or h < 100:
            w, h = self.canvas.winfo_screenwidth(), self.canvas.winfo_screenheight()
        self.canvas.create_text(w // 2, h // 2, text=s, fill=color,
                                font=("Microsoft YaHei", size), width=w * 0.82,
                                justify="center")

    def _cross(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 100 or h < 100:
            w, h = self.canvas.winfo_screenwidth(), self.canvas.winfo_screenheight()
        cx, cy = w // 2, h // 2
        s = min(w, h) * 0.045
        self.canvas.create_line(cx - s, cy, cx + s, cy, fill="#9ca3af", width=3)
        self.canvas.create_line(cx, cy - s, cx, cy + s, fill="#9ca3af", width=3)

    def _arrow(self, label):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 100 or h < 100:
            w, h = self.canvas.winfo_screenwidth(), self.canvas.winfo_screenheight()
        cx, cy = w // 2, h // 2
        L = min(w, h) * 0.24
        color = "#5eead4"
        lw = max(8, int(h * 0.014))
        if label == 0:      # 左手：箭头指左
            tip, base = cx - L, cx - L * 0.52
            self.canvas.create_line(cx + L * 0.42, cy, base, cy,
                                    fill=color, width=lw, capstyle="round")
            self.canvas.create_polygon(tip, cy, base, cy - h * 0.10,
                                       base, cy + h * 0.10, fill=color, outline="")
            txt = "左手"
        else:               # 右手
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
        self.root.after(2500, self.root.destroy)

    def _show_error(self, msg):
        self._clear()
        self._text(msg, 24, "#fca5a5")
        self.root.after(4000, self.root.destroy)

    def _abort(self):
        self.aborted = True
        try:
            self.root.destroy()
        except Exception:
            pass


def record_loop(acq, duration):
    t0 = time.perf_counter()
    last = 0.0
    while not acq.done.is_set():
        if duration and time.perf_counter() - t0 >= duration:
            break
        time.sleep(0.2)
        el = time.perf_counter() - t0
        if el - last >= 2.0:
            print("  " + acq.status_line())
            last = el


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="OpenBCI Cyton 实采记录器 + 左右手 MI 范式",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="列出串口后退出")
    ap.add_argument("--port", default=None, help="Cyton Dongle 串口，如 COM3")
    ap.add_argument("--input", default=None, help="离线模式：file:xxx.bin")
    ap.add_argument("--out", default=None, help="输出目录（默认 data/raw/openbci/<日期>）")
    ap.add_argument("--name", default=None, help="文件前缀（默认 subj_时间）")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="无范式时采多少秒（0=直到 Ctrl+C / 文件结束）")
    ap.add_argument("--paradigm", choices=["none", "mi2"], default="none",
                    help="mi2=左右手运动想象")
    ap.add_argument("--trials-per-class", type=int, default=10,
                    help="每类每组的 trial 数（默认 10 → 每组 20）")
    ap.add_argument("--blocks", type=int, default=3)
    ap.add_argument("--fixation", type=float, default=3.0)
    ap.add_argument("--cue", type=float, default=1.25)
    ap.add_argument("--imagery", type=float, default=4.0)
    ap.add_argument("--rest", type=float, default=4.0)
    ap.add_argument("--rest-jitter", type=float, default=1.0)
    ap.add_argument("--block-rest", type=float, default=20.0)
    ap.add_argument("--windowed", action="store_true", help="窗口模式（调试用）")
    ap.add_argument("--auto-start", action="store_true",
                    help="跳过“按空格开始”，进入后自动开跑（排练/自测用）")
    ap.add_argument("--hw-marker", action="store_true",
                    help="用 Cyton /4 + AUX1 硬件 marker（需固件支持）")
    ap.add_argument("--no-csv", action="store_true", help="只存 .bin，不写逐样本 CSV")
    a = ap.parse_args(argv)

    if a.list:
        list_ports()
        return 0

    infile = a.input
    if infile and infile.startswith("file:"):
        infile = infile[5:]
    if a.port and infile:
        print("✗ --port 和 --input 只能选一个")
        return 2
    if not a.port and not infile:
        list_ports()
        print("\n✗ 请用 --port COMx 或 --input file:xxx.bin")
        return 2

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = a.out or os.path.join(ROOT, "data", "raw", "openbci", stamp[:8])
    name = sanitize(a.name or f"subj_{stamp}")

    acq = Acquisition(port=a.port, infile=infile, out_dir=out_dir, name=name,
                      hw_marker=a.hw_marker, write_csv=not a.no_csv)
    try:
        acq.open()
    except Exception as e:
        print(f"✗ 打开失败：{e}")
        return 2

    print("=" * 72)
    print("OpenBCI 实采记录")
    print("=" * 72)
    print(f"  源      : {('串口 ' + a.port) if a.port else ('文件 ' + infile)}")
    print(f"  输出    : {out_dir}\\{name}_*")
    print(f"  硬件marker: {'开' if a.hw_marker else '关'}（AUX1 需要 /4 marker 模式）")
    print(f"  通道    : CH1..CH8 = {', '.join(CH_NAMES)}（项目 3 通道用 CH1-3）")
    print()

    acq.start()
    if not acq.wait_ready(6.0):
        print("  ⚠ 6 s 内没等到数据；如果串口选错会一直空转，Ctrl+C 退出。")
    if acq.error is not None:
        print(f"  ✗ 采集线程出错：{acq.error}")

    try:
        if a.paradigm == "mi2":
            p = MIParadigm(acq, out_dir, name,
                           trials_per_class=a.trials_per_class,
                           blocks=a.blocks, fixation=a.fixation, cue=a.cue,
                           imagery=a.imagery, rest=a.rest,
                           rest_jitter=a.rest_jitter,
                           block_rest=a.block_rest, windowed=a.windowed,
                           auto_start=a.auto_start)
            try:
                p.run()
            finally:
                p.close()
        else:
            record_loop(acq, a.duration)
    except KeyboardInterrupt:
        print("\n  收到 Ctrl+C，停止。")
    finally:
        acq.stop()
        meta = acq.write_meta()
        print()
        print("  " + acq.status_line())
        print(f"  原始流 : {acq.bin_path}")
        if acq.csv_path:
            print(f"  逐样本 : {acq.csv_path}")
        ev = os.path.join(out_dir, f"{name}_events.csv")
        if os.path.exists(ev):
            print(f"  事件表 : {ev}")
        print(f"  元数据 : {acq.meta_path}")
        if meta["lost_pct"] > 1.0:
            print(f"  ⚠ 丢包 {meta['lost_pct']:.2f}% > 1%，这份记录按 OpenBCI 口径不可用。")
        elif meta["lost_pct"] > 0:
            print(f"  丢包 {meta['lost_pct']:.3f}%（可接受，<1%）")
        else:
            print("  ✓ 序号连续，无丢包")
    return 0


if __name__ == "__main__":
    sys.exit(main())
