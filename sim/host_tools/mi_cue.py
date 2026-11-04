#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
左右手运动想象提示器（独立版，不需要 OpenBCI）

用途
────
给被试做左右手 MI 的视觉提示：中央十字 → 左/右箭头 → 持续想象 → 休息。
时序按 BCI IV 2b 对齐：3 s 注视、cue 1.25 s、想象 4 s、休息 4 s。
不依赖 numpy / matplotlib / pyserial，只用 Python 标准库 + tkinter。

运行
────
  H:/FPGA2026QS/.venv/Scripts/python.exe H:/FPGA2026QS/tools/mi_cue.py

  # 窗口模式（方便调试/放到第二块屏）
  ... mi_cue.py --windowed

  # 自动开始 + 截图（开发用）
  ... mi_cue.py --windowed --auto-start --screenshot shot.png --shot-delay 3

按键
────
  空格     开始 / 暂停 / 继续
  Esc      中止
  F11      切换全屏

输出
────
  默认写 sessions/mi_cue/<name>_events.csv 和 _meta.json；
  只记提示时间，不碰 EEG。要严格对齐 EEG，请用 openbci_testbench.py 的 MI 页。
"""
import argparse
import csv
import ctypes
import datetime
import json
import os
import random
import sys
import threading
import time
import tkinter as tk

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Windows 高 DPI：Tk 坐标和截图一致
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

BG = "#000000"
TEXT = "#e5e7eb"
MUTED = "#9ca3af"
DIM = "#374151"
ACCENT = "#5eead4"
WARN = "#fbbf24"

CLASS_NAME = {0: "left", 1: "right"}
CLASS_CN = {0: "左手", 1: "右手"}


def safe_name(s):
    bad = '<>:"/\\|?* '
    out = "".join("_" if c in bad else c for c in str(s)).strip("_")
    return out or "mi"


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def build_trials(trials_per_class, blocks):
    """每组左右各 N 个，块内打乱且不出现 3 连同类。"""
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


def build_phases(trials, blocks, prepare, fixation, cue, imagery, rest,
                 rest_jitter, block_rest):
    ph = []
    for b in range(blocks):
        ph.append(("block_start", prepare, None, b))
        for t in [x for x in trials if x["block"] == b]:
            ph.append(("trial_start", fixation, t, b))
            ph.append(("cue", cue, t, b))
            ph.append(("imagery", max(0.0, imagery - cue), t, b))
            r = max(1.0, rest + random.uniform(-rest_jitter, rest_jitter))
            ph.append(("rest", r, t, b))
        ph.append(("block_end", block_rest, None, b))
    return ph


class EventWriter:
    def __init__(self, path):
        self.path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.f = open(path, "w", newline="", encoding="utf-8-sig")
        self.w = csv.writer(self.f)
        self.w.writerow(["stage", "trial", "block", "label", "class", "event",
                         "host_time", "rel_s"])

    def add(self, stage, trial, block, label, event, t0):
        self.w.writerow([
            stage,
            "" if trial is None else trial,
            "" if block is None else block,
            "" if label is None else label,
            "" if label is None else CLASS_NAME.get(label, ""),
            event,
            f"{time.time():.6f}",
            f"{max(0.0, time.perf_counter() - t0):.3f}",
        ])
        self.f.flush()

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


class CueApp:
    def __init__(self, root, args):
        self.root = root
        self.args = args
        self.root.title("左右手运动想象提示器")
        self.root.configure(bg=BG)
        self.root.geometry(args.geometry)
        self.root.protocol("WM_DELETE_WINDOW", self.abort)

        self.trials = build_trials(args.trials_per_class, args.blocks)
        self.blocks = args.blocks
        self.phases = build_phases(
            self.trials, args.blocks, args.prepare, args.fixation, args.cue,
            args.imagery, args.rest, args.rest_jitter, args.block_rest)

        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.root.update_idletasks()
        self.canvas.bind("<Configure>", self._on_resize)

        self.started = False
        self.paused = False
        self.finished = False
        self.aborted = False
        self.after_id = None
        self.remaining_ms = 0
        self.deadline = 0.0
        self.phase = None
        self.cur = None
        self.cur_block = None
        self.idx = 0
        self.t0 = 0.0
        self.events = None
        self.fullscreen = not args.windowed

        self._set_fullscreen(self.fullscreen)
        self._bind()
        self._intro()

        if args.auto_start:
            self.root.after(300, self.start)
        if args.screenshot:
            self.root.after(int(args.shot_delay * 1000),
                            lambda: self._screenshot(args.screenshot))

    # ── 窗口 ──────────────────────────────────────────────
    def _set_fullscreen(self, on):
        self.fullscreen = bool(on)
        try:
            self.root.attributes("-fullscreen", self.fullscreen)
        except Exception:
            pass
        if not self.fullscreen:
            self.root.geometry(self.args.geometry)
            self.root.configure(cursor="arrow")
        else:
            self.root.configure(cursor="none")

    def _toggle_fullscreen(self, _evt=None):
        self._set_fullscreen(not self.fullscreen)

    def _bind(self):
        self.root.bind("<space>", self._space)
        self.root.bind("<Escape>", lambda e: self.abort())
        self.root.bind("<F11>", self._toggle_fullscreen)

    # ── 路径 / 元数据 ─────────────────────────────────────
    def _paths(self):
        if self.args.events:
            ev = os.path.abspath(self.args.events)
        else:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            name = safe_name(self.args.name or f"mi_{stamp}")
            out = os.path.abspath(self.args.out) if self.args.out else os.path.join(
                ROOT, "sessions", "mi_cue")
            ev = os.path.join(out, f"{name}_events.csv")
        base = ev[:-len("_events.csv")] if ev.endswith("_events.csv") else os.path.splitext(ev)[0]
        meta = base + "_meta.json"
        return ev, meta

    def _write_meta(self, status):
        if self.args.no_events:
            return
        ev, meta = self._paths()
        data = {
            "created": now_iso(),
            "status": status,
            "elapsed_s": round(time.perf_counter() - self.t0, 3) if self.t0 else 0.0,
            "params": {
                "trials_per_class_per_block": self.args.trials_per_class,
                "blocks": self.args.blocks,
                "prepare_s": self.args.prepare,
                "fixation_s": self.args.fixation,
                "cue_s": self.args.cue,
                "imagery_s": self.args.imagery,
                "rest_s": self.args.rest,
                "rest_jitter_s": self.args.rest_jitter,
                "block_rest_s": self.args.block_rest,
                "seed": self.args.seed,
            },
            "n_trials": len(self.trials),
            "trials": self.trials,
            "events_csv": ev,
            "note": "standalone visual cue; no EEG/trigger output",
        }
        try:
            os.makedirs(os.path.dirname(meta), exist_ok=True)
            with open(meta, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"写 meta 失败：{e}")

    # ── 流程 ──────────────────────────────────────────────
    def start(self):
        if self.started or self.finished:
            return
        self.started = True
        self.t0 = time.perf_counter()
        if not self.args.no_events:
            ev, _ = self._paths()
            try:
                self.events = EventWriter(ev)
                print(f"事件表：{ev}")
            except Exception as e:
                print(f"事件表打不开：{e}")
                self.events = None
        self._write_meta("running")
        self._next()

    def _space(self, _evt=None):
        if not self.started:
            self.start()
            return
        if self.finished or self.aborted:
            return
        if not self.paused:
            if self.after_id is not None:
                self.root.after_cancel(self.after_id)
                self.after_id = None
            self.remaining_ms = max(0, int((self.deadline - time.perf_counter()) * 1000))
            self.paused = True
            self._log("pause")
            self._draw_pause()
        else:
            self.paused = False
            self._log("resume")
            self._draw_phase()
            ms = max(1, self.remaining_ms)
            self.deadline = time.perf_counter() + ms / 1000.0
            self.after_id = self.root.after(ms, self._next)

    def _next(self):
        if self.aborted or self.finished or self.paused:
            return
        # 离开 rest 时补一个 rest_end
        if self.phase == "rest" and self.cur is not None:
            self._log("rest_end")
        if self.idx >= len(self.phases):
            self.finish()
            return

        phase, dur, trial, block = self.phases[self.idx]
        self.idx += 1
        self.phase = phase
        self.cur = trial
        self.cur_block = block
        self._draw_phase()
        self._log_phase_start()
        ms = max(1, int(round(dur * 1000)))
        self.remaining_ms = ms
        self.deadline = time.perf_counter() + ms / 1000.0
        self.after_id = self.root.after(ms, self._next)

    def _log_phase_start(self):
        if self.phase == "block_start":
            self._log("block_start")
        elif self.phase == "trial_start":
            self._log("trial_start")
        elif self.phase == "cue":
            self._log("cue")
            if self.args.beep:
                threading.Thread(target=self._beep, daemon=True).start()
        elif self.phase == "imagery":
            self._log("cue_end")
        elif self.phase == "rest":
            self._log("imagery_end")
        elif self.phase == "block_end":
            self._log("block_end")

    def _log(self, event):
        if self.events is None:
            return
        t = self.cur
        try:
            self.events.add("main", None if t is None else t["idx"],
                            self.cur_block, None if t is None else t["label"],
                            event, self.t0)
        except Exception:
            pass

    def _beep(self):
        try:
            import winsound
            winsound.Beep(880, 55)
        except Exception:
            try:
                self.root.after(0, self.root.bell)
            except Exception:
                pass

    def finish(self):
        if self.finished:
            return
        self.finished = True
        self._log("finish")
        self._write_meta("finished")
        self._show("实验结束\n\n感谢配合", 38)
        self.root.after(3500, self._close)

    def abort(self):
        if self.finished:
            self._close()
            return
        if self.aborted:
            return
        if not self.started:
            self._close()
            return
        self.aborted = True
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        self._log("abort")
        self._write_meta("aborted")
        self._show("已中止\n\n可以关闭窗口", 30, WARN)
        self.root.after(1200, self._close)

    def _close(self):
        if self.events is not None:
            self.events.close()
            self.events = None
        try:
            self.root.destroy()
        except Exception:
            pass

    # ── 画面 ──────────────────────────────────────────────
    def _size(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 100 or h < 100:
            w, h = self.root.winfo_width(), self.root.winfo_height()
        if w < 100 or h < 100:
            w, h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        return w, h

    def _on_resize(self, _evt=None):
        if not hasattr(self, "started"):
            return
        try:
            if not self.started:
                self._intro()
            elif self.paused:
                self._draw_pause()
            elif not self.finished and not self.aborted:
                self._draw_phase()
        except Exception:
            pass

    def _clear(self):
        self.canvas.delete("all")

    def _text(self, s, size=28, color=TEXT, y=None, bold=False):
        w, h = self._size()
        yy = h // 2 if y is None else y
        font = ("Microsoft YaHei", size, "bold" if bold else "normal")
        self.canvas.create_text(w // 2, yy, text=s, fill=color, font=font,
                                width=w * 0.82, justify="center")

    def _intro(self):
        self._clear()
        w, h = self._size()
        self._text("左右手运动想象", max(32, int(h * 0.045)), TEXT, int(h * 0.14), True)
        body = (
            "屏幕中央会出现十字，请一直看着它。\n"
            f"{self.args.fixation:g} 秒后出现箭头：\n"
            "    箭头向左 → 想象左手\n"
            "    箭头向右 → 想象右手\n\n"
            "想象手在张开、握紧的感觉，手不要真的动。\n"
            "头、眼睛、下巴保持不动，自然眨眼。\n"
            "每组结束休息一会儿。"
        )
        self._text(body, max(15, int(h * 0.020)), TEXT, int(h * 0.47))
        total = len(self.trials)
        est = (total * (self.args.fixation + self.args.imagery + self.args.rest)
               + self.args.blocks * (self.args.prepare + self.args.block_rest)) / 60.0
        foot = (f"{self.args.blocks} 组 × 每组 {self.args.trials_per_class * 2} trial"
                f" = {total} trial，约 {est:.1f} 分钟\n"
                f"空格 开始     Esc 退出     F11 全屏")
        self._text(foot, max(11, int(h * 0.015)), MUTED, int(h * 0.89))
        if not self.args.no_events and self.args.debug:
            ev, _ = self._paths()
            self.canvas.create_text(w // 2, h - 18, text=f"事件表：{ev}",
                                    fill=DIM, font=("Consolas", 9))

    def _draw_phase(self):
        self._clear()
        w, h = self._size()
        if self.args.debug:
            t = self.cur
            info = (f"T{(t['idx'] + 1) if t else '-'} / {len(self.trials)}   "
                    f"{self.phase}   {CLASS_CN.get(t['label'], '') if t else ''}")
            self.canvas.create_text(18, 16, text=info, anchor="nw", fill=DIM,
                                    font=("Consolas", 11))

        if self.phase == "block_start":
            self._text(f"第 {self.cur_block + 1} / {self.blocks} 组\n\n准备…",
                       max(30, int(h * 0.042)))
        elif self.phase == "trial_start":
            self._cross()
        elif self.phase == "cue":
            label = self.cur["label"]
            self._arrow(label)
            if self.args.label:
                self.canvas.create_text(w // 2, h // 2 + h * 0.19,
                                        text=CLASS_CN[label], fill="#cbd5e1",
                                        font=("Microsoft YaHei", max(30, int(h * 0.035))))
        elif self.phase == "imagery":
            self._cross()
        elif self.phase == "rest":
            # 黑屏；只留一个非常暗的中心点，避免完全丢失注视位置
            self.canvas.create_oval(w // 2 - 2, h // 2 - 2, w // 2 + 2, h // 2 + 2,
                                    fill="#1f2937", outline="")
        elif self.phase == "block_end":
            self._text(f"本组结束\n\n休息 {int(round(self.args.block_rest))} 秒",
                       max(30, int(h * 0.042)))

    def _draw_pause(self):
        self._clear()
        self._text("已暂停\n\n空格 继续     Esc 退出", 32, WARN)
        if self.args.debug and self.cur is not None:
            self.canvas.create_text(18, 16, anchor="nw", fill=DIM,
                                    text=f"T{self.cur['idx'] + 1} / {len(self.trials)}  {self.phase}",
                                    font=("Consolas", 11))

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
        color = ACCENT
        lw = max(8, int(h * 0.014))
        if label == 0:
            tip, base = cx - L, cx - L * 0.52
            self.canvas.create_line(cx + L * 0.42, cy, base, cy,
                                    fill=color, width=lw, capstyle="round")
            self.canvas.create_polygon(tip, cy, base, cy - h * 0.10,
                                       base, cy + h * 0.10, fill=color, outline="")
        else:
            tip, base = cx + L, cx + L * 0.52
            self.canvas.create_line(cx - L * 0.42, cy, base, cy,
                                    fill=color, width=lw, capstyle="round")
            self.canvas.create_polygon(tip, cy, base, cy - h * 0.10,
                                       base, cy + h * 0.10, fill=color, outline="")

    def _show(self, msg, size=32, color=TEXT):
        self._clear()
        self._text(msg, size, color)

    # ── 开发用截图 ────────────────────────────────────────
    def _screenshot(self, path):
        try:
            from PIL import ImageGrab
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.focus_force()
            self.root.update_idletasks()
            self.root.update()
            time.sleep(0.6)
            x = self.root.winfo_rootx()
            y = self.root.winfo_rooty()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
            print(f"截图：{path}")
        except Exception as e:
            print(f"截图失败：{e}")
        self.root.after(300, self.abort)


def main():
    ap = argparse.ArgumentParser(
        description="左右手运动想象提示器（独立视觉提示，不接 OpenBCI）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials-per-class", type=int, default=10,
                    help="每组每类 trial 数（默认 10 → 每组 20，3 组共 60）")
    ap.add_argument("--blocks", type=int, default=3)
    ap.add_argument("--prepare", type=float, default=3.0, help="每组开始前的准备秒数")
    ap.add_argument("--fixation", type=float, default=3.0, help="trial 起点到 cue 的注视秒数")
    ap.add_argument("--cue", type=float, default=1.25)
    ap.add_argument("--imagery", type=float, default=4.0, help="从 cue 开始算的想象总时长")
    ap.add_argument("--rest", type=float, default=4.0)
    ap.add_argument("--rest-jitter", type=float, default=1.0)
    ap.add_argument("--block-rest", type=float, default=20.0)
    ap.add_argument("--windowed", action="store_true", help="窗口模式；可用 --geometry 放第二块屏")
    ap.add_argument("--geometry", default="1100x720+80+40")
    ap.add_argument("--no-label", action="store_false", dest="label",
                    help="不显示箭头下方的“左手/右手”文字")
    ap.add_argument("--beep", action="store_true", help="cue 出现时响一声")
    ap.add_argument("--debug", action="store_true", help="左上角显示 trial/phase")
    ap.add_argument("--auto-start", action="store_true", help="跳过空格，直接开始（排练/自测）")
    ap.add_argument("--out", default=None, help="输出目录（默认 sessions/mi_cue）")
    ap.add_argument("--name", default=None, help="文件名前缀")
    ap.add_argument("--events", default=None, help="直接指定 events.csv 路径")
    ap.add_argument("--no-events", action="store_true", help="不写 CSV/meta")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（复现顺序）")
    ap.add_argument("--screenshot", default=None, help="启动后截图并退出（开发用）")
    ap.add_argument("--shot-delay", type=float, default=3.0)
    a = ap.parse_args()

    if a.trials_per_class < 1 or a.blocks < 1:
        print("trials-per-class 和 blocks 必须是正整数")
        return 2
    if a.seed is not None:
        random.seed(a.seed)
    if not a.no_events and a.events is None:
        print("提示：事件表默认写到 sessions/mi_cue/；可用 --events 指定")

    root = tk.Tk()
    app = CueApp(root, a)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
