#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""mi_arrow.py —— 只显示左右手箭头的运动想象提示程序（不连接任何串口/硬件）

和 sim/host_tools/openbci_record.py 里的 MI 范式完全同一套时序（3 s 注视 + 1.25 s 箭头
+ 4 s 想象 + 4~5 s 休息 + 组间 20 s），但：
  · 不打开串口、不依赖 OpenBCI，只负责"给被试看箭头 + 记事件表"；
  · 事件表带 wall time / ISO 时间 / 相对秒数，方便和另一台机器上的 EEG 记录做时间对齐；
  · 默认 1 组 20 trial（左右各 10），适合"先测试一组"的排练/小样本验证。

用法:
  python sim/host_tools/mi_arrow.py                      # 默认 1 组 20 trial，全屏
  python sim/host_tools/mi_arrow.py --windowed           # 窗口模式（排练/调试）
  python sim/host_tools/mi_arrow.py --auto-start         # 跳过"按空格开始"
  python sim/host_tools/mi_arrow.py --blocks 3 --trials-per-class 10 --name subj01_mi
  python sim/host_tools/mi_arrow.py --dry-run            # 只打印时序，不弹窗（自检用）

操作: 空格 开始；Esc 中止（已记录的事件表保留）。

输出（默认 sessions/arrows/<yyyyMMdd_HHMMSS>/）:
  <name>_events.csv   trial,block,label,class,event,wall_time,iso_time,rel_s
  <name>_meta.json    参数、开始时间、事件文件路径
"""
import argparse
import csv
import datetime as dt
import json
import os
import random
import sys
import time

CLASS_NAME = {0: "left", 1: "right"}


def iso_now():
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def build_mi_trials(trials_per_class, blocks, seed=None):
    """每块内左右 1:1 随机，且尽量不出现连续 3 个同类（与 openbci_record.py 一致）。"""
    rng = random.Random(seed)
    trials = []
    for b in range(blocks):
        labels = [0] * trials_per_class + [1] * trials_per_class
        rng.shuffle(labels)
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
                    rest=4.0, rest_jitter=1.0, block_rest=20.0, seed=None):
    """(phase, duration, trial, block) 序列；phase ∈ block_start/trial_start/cue/imagery/rest/block_end。"""
    rng = random.Random(None if seed is None else seed + 1)
    ph = []
    for b in range(blocks):
        ph.append(("block_start", 3.0, None, b))
        for t in [x for x in trials if x["block"] == b]:
            ph.append(("trial_start", fixation, t, b))
            ph.append(("cue", cue, t, b))
            ph.append(("imagery", max(0.0, imagery - cue), t, b))
            r = rest + rng.uniform(-rest_jitter, rest_jitter)
            ph.append(("rest", max(1.0, r), t, b))
        ph.append(("block_end", block_rest, None, b))
    return ph


class EventLog:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "w", newline="", encoding="utf-8-sig")
        self.w = csv.writer(self.f)
        self.w.writerow(["trial", "block", "label", "class", "event",
                         "wall_time", "iso_time", "rel_s"])
        self.t0 = None

    def add(self, trial="", block="", label="", cls="", event=""):
        now = time.time()
        if self.t0 is None:
            self.t0 = now
        self.w.writerow([trial, block, label, cls, event,
                         "%.6f" % now, iso_now(), "%.3f" % (now - self.t0)])
        self.f.flush()

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


class ArrowParadigm:
    def __init__(self, out_dir, name, trials_per_class=10, blocks=1,
                 fixation=3.0, cue=1.25, imagery=4.0, rest=4.0,
                 rest_jitter=1.0, block_rest=20.0, windowed=False,
                 auto_start=False, seed=None):
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
        self.seed = seed
        self.trials = build_mi_trials(trials_per_class, blocks, seed)
        self.phases = build_mi_phases(self.trials, blocks, fixation, cue, imagery,
                                      rest, rest_jitter, block_rest, seed)
        self.events = None
        self.idx = 0
        self.started = False
        self.aborted = False
        self.cur = None
        self.root = None
        self.tk = None

    def run(self):
        import tkinter as tk
        self.tk = tk
        self.events = EventLog(os.path.join(self.out_dir, self.name + "_events.csv"))
        root = tk.Tk()
        self.root = root
        root.title("MI arrows")
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
        self.events.close()

    # ── 画面 ──
    def _size(self):
        w = self.canvas.winfo_width(); h = self.canvas.winfo_height()
        if w < 100 or h < 100:
            w = self.canvas.winfo_screenwidth(); h = self.canvas.winfo_screenheight()
        return w, h

    def _intro(self):
        self.canvas.delete("all")
        w, h = self._size()
        txt = ("左右手运动想象（箭头提示）\n\n"
               "1. 盯住屏幕中央的十字，身体放松，不要动。\n"
               "2. 出现箭头时，想象箭头所指那只手在张开、握紧。\n"
               "3. 想象“动的感觉”，不要真的动，也不要看自己的手。\n"
               "4. 头、眼睛、下巴保持不动，自然眨眼。\n\n"
               "按 空格 开始    Esc 中止")
        self.canvas.create_text(w // 2, h // 2, text=txt, fill="#e5e7eb",
                                font=("Microsoft YaHei", 26), width=int(w * 0.82),
                                justify="center")
        self.root.bind("<space>", self._on_space)

    def _on_space(self, _evt=None):
        if self.started:
            return
        self.started = True
        self.root.unbind("<space>")
        self._next()

    def _clear(self):
        self.canvas.delete("all")

    def _text(self, s, size=30, color="#e5e7eb"):
        w, h = self._size()
        self.canvas.create_text(w // 2, h // 2, text=s, fill=color,
                                font=("Microsoft YaHei", size), width=int(w * 0.82),
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
        s = min(w, h) * 0.12
        if label == 0:      # 左手：箭头指左
            pts = [(cx - s, cy), (cx + s * 0.9, cy - s * 0.75),
                   (cx + s * 0.9, cy - s * 0.28), (cx + s * 0.9, cy + s * 0.28),
                   (cx + s * 0.9, cy + s * 0.75)]
        else:               # 右手：箭头指右
            pts = [(cx + s, cy), (cx - s * 0.9, cy - s * 0.75),
                   (cx - s * 0.9, cy - s * 0.28), (cx - s * 0.9, cy + s * 0.28),
                   (cx - s * 0.9, cy + s * 0.75)]
        self.canvas.create_polygon(pts, fill="#f59e0b", outline="#f59e0b")

    # ── 主循环 ──
    def _next(self):
        if self.aborted:
            return
        if self.idx >= len(self.phases):
            self._finish()
            return
        phase, dur, trial, block = self.phases[self.idx]
        self.idx += 1
        self._on_phase(phase, dur, trial, block)
        self.root.after(max(1, int(round(dur * 1000.0))), self._next)

    def _on_phase(self, phase, dur, trial, block):
        ev = self.events
        if phase == "block_start":
            self._clear()
            self._text("第 %d / %d 组\n\n准备…" % (block + 1, self.blocks), 34)
            ev.add(block=block, event="block_start")
        elif phase == "trial_start":
            self.cur = dict(trial)
            self._clear(); self._cross()
            ev.add(trial=trial["idx"], block=block, label=trial["label"],
                   cls=CLASS_NAME[trial["label"]], event="trial_start")
        elif phase == "cue":
            self._clear(); self._arrow(trial["label"])
            ev.add(trial=trial["idx"], block=block, label=trial["label"],
                   cls=CLASS_NAME[trial["label"]], event="cue")
        elif phase == "imagery":
            self._clear(); self._cross()
            ev.add(trial=trial["idx"], block=block, label=trial["label"],
                   cls=CLASS_NAME[trial["label"]], event="cue_end")
        elif phase == "rest":
            self._clear()
            ev.add(trial=trial["idx"], block=block, label=trial["label"],
                   cls=CLASS_NAME[trial["label"]], event="imagery_end")
            ev.add(trial=trial["idx"], block=block, label=trial["label"],
                   cls=CLASS_NAME[trial["label"]], event="rest_end")
            self.cur = None
        elif phase == "block_end":
            self._clear()
            self._text("本组结束\n\n休息 %d 秒" % int(round(self.block_rest)), 34)
            ev.add(block=block, event="block_end")

    def _abort(self):
        self.aborted = True
        try:
            if self.events:
                self.events.add(event="abort")
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass

    def _finish(self):
        print("  ✓ 全部完成：%d 组 × %d trial" % (self.blocks, 2 * self.trials_per_class))
        self._clear()
        self._text("实验结束\n\n感谢配合！\n\n按 Esc 关闭", 32)
        self.events.add(event="finish")
        self.root.bind("<Escape>", lambda e: self.root.destroy())


def main(argv=None):
    ap = argparse.ArgumentParser(description="只显示左右手箭头的 MI 提示程序（不连串口）")
    ap.add_argument("--out", default=None, help="输出目录（默认 sessions/arrows/<时间戳>）")
    ap.add_argument("--name", default="mi_arrow", help="文件前缀（默认 mi_arrow）")
    ap.add_argument("--trials-per-class", type=int, default=10, help="每类每组的试次数（默认 10 → 每组 20）")
    ap.add_argument("--blocks", type=int, default=1, help="组数（默认 1 组 20 trial）")
    ap.add_argument("--fixation", type=float, default=3.0)
    ap.add_argument("--cue", type=float, default=1.25)
    ap.add_argument("--imagery", type=float, default=4.0)
    ap.add_argument("--rest", type=float, default=4.0)
    ap.add_argument("--rest-jitter", type=float, default=1.0)
    ap.add_argument("--block-rest", type=float, default=20.0)
    ap.add_argument("--windowed", action="store_true", help="窗口模式（默认全屏）")
    ap.add_argument("--auto-start", action="store_true", help="跳过按空格，进入后自动开始（排练用）")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（复现试次顺序）")
    ap.add_argument("--dry-run", action="store_true", help="只打印时序，不弹窗")
    a = ap.parse_args(argv)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = a.out or os.path.join(root, "sessions", "arrows", stamp)
    name = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in (a.name or "mi_arrow"))

    trials = build_mi_trials(a.trials_per_class, a.blocks, a.seed)
    phases = build_mi_phases(trials, a.blocks, a.fixation, a.cue, a.imagery,
                             a.rest, a.rest_jitter, a.block_rest, a.seed)

    if a.dry_run:
        print("=" * 74)
        print("mi_arrow dry-run：不弹窗、不连串口，只打印时序")
        print("  输出目录: %s" % out_dir)
        print("  %d 组 × %d trial（左右各 %d），总时长约 %.1f 分钟" % (
            a.blocks, 2 * a.trials_per_class, a.trials_per_class,
            sum(p[1] for p in phases) / 60.0))
        print("=" * 74)
        print("  %-4s %-5s %-5s %-11s %s" % ("trial", "block", "label", "event", "duration_s"))
        for phase, dur, trial, block in phases:
            print("  %-4s %-5s %-5s %-11s %.2f" % (
                "" if trial is None else trial["idx"],
                block,
                "" if trial is None else ("L" if trial["label"] == 0 else "R"),
                phase, dur))
        nL = sum(1 for t in trials if t["label"] == 0)
        print("  合计: %d trial（左手 %d / 右手 %d），约 %.1f 分钟" % (
            len(trials), nL, len(trials) - nL, sum(p[1] for p in phases) / 60.0))
        return 0

    os.makedirs(out_dir, exist_ok=True)
    meta = {
        "created": iso_now(),
        "program": "sim/host_tools/mi_arrow.py (arrow-only, no serial)",
        "params": {
            "trials_per_class": a.trials_per_class, "blocks": a.blocks,
            "fixation_s": a.fixation, "cue_s": a.cue, "imagery_s": a.imagery,
            "rest_s": a.rest, "rest_jitter_s": a.rest_jitter,
            "block_rest_s": a.block_rest, "seed": a.seed,
        },
        "n_trials": len(trials),
        "events_csv": os.path.join(out_dir, name + "_events.csv"),
        "note": "standalone visual cue; no EEG/trigger output. 用 wall_time/iso_time/rel_s 与另一台记录设备对齐。",
    }
    with open(os.path.join(out_dir, name + "_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("=" * 74)
    print("MI 箭头提示（不连接串口）")
    print("  输出 : %s" % out_dir)
    print("  范式 : %d 组 × %d trial（左右各 %d），注视 %.1f s + 箭头 %.2f s + 想象 %.1f s + 休息 %.1f±%.1f s"
          % (a.blocks, 2 * a.trials_per_class, a.trials_per_class,
             a.fixation, a.cue, a.imagery, a.rest, a.rest_jitter))
    print("  操作 : 空格 开始；Esc 中止")
    print("=" * 74)

    p = ArrowParadigm(out_dir, name, a.trials_per_class, a.blocks,
                      a.fixation, a.cue, a.imagery, a.rest, a.rest_jitter,
                      a.block_rest, a.windowed, a.auto_start, a.seed)
    try:
        p.run()
    except KeyboardInterrupt:
        print("\n  收到 Ctrl+C，停止。")
    finally:
        if p.events:
            p.events.close()
        print("  事件表: %s" % os.path.join(out_dir, name + "_events.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
