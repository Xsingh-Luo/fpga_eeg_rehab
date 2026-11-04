#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Cyton 模拟器 —— 用本地开源数据集模拟 OpenBCI Cyton 的串口输出，喂给 Zynq PS

用途
────
在没有 Cyton 硬件的情况下，让 Zynq 端的 UART 接收 + 包解析模块有真实数据可调试。
数据来源是 BCI Competition IV 2b；输出格式严格按 Cyton 固件的
`sendChannelDataSerial()` 构造 33 字节包。

包格式（33 字节 @ 250 Hz）
──────────────────────────
  偏移  长度  内容
   0     1    帧头。**默认 0x41**（Cyton 固件 OPENBCI_BOP='A'，PIC32→RFduino 那条
              UART 上的首字节）；用 --header a0 可切换成 0xA0（dongle 输出给 PC 的格式）
   1     1    采样序号 0-255 回绕
   2    24    8 通道 × 24 位有符号，**大端，MSB 在前**
  26     6    AUX 字节，含义由帧尾决定（见 --aux）
  32     1    帧尾 = 0xC0 | packetType

  换算：count = µV / 0.022352   （4.5V / gain24 / (2^23-1) = 0.022352 µV/count）

接线（CH340 → Zynq PS）
───────────────────────
  CH340 TX ──► Zynq PS UART RX
  CH340 GND ── Zynq GND（必须共地）
  ★ CH340 模块如果有 5V/3.3V 跳线，**必须切到 3.3V**，否则可能打坏 Zynq

用法
────
  # 列出串口
  python cyton_sim.py --list

  # 干跑：只看前几包的十六进制，不发送
  python cyton_sim.py --dry-run --n 5

  # 发到 COM3，等 Zynq 发 'b' 再开始（模拟真实 Cyton 行为）
  python cyton_sim.py --port COM3

  # 直接开跑不等命令，用被试 4 的数据
  python cyton_sim.py --port COM3 --subject 4 --no-wait

  # 本地自测：配合 com0com 虚拟串口对
  python cyton_sim.py --port COM9 --no-wait
  # 另一个窗口读 COM10 验证

  # 回放实采 .bin（默认把 0xA0 帧头改写为 0x41）
  python cyton_sim.py --source bin --bin raw.bin --port COM3
"""
import argparse
import os
import queue
import struct
import sys
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np

try:
    from cyton_parse import CytonParser
except ImportError:                      # 以 tools.cyton_sim 形式导入时
    from .cyton_parse import CytonParser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GDF_DIR = os.path.join(ROOT, "data", "raw", "BCICIV_2b_gdf")
MAT_DIR = os.path.join(ROOT, "matlab", "data")

FS = 250.0
UV_PER_COUNT = 0.022352          # 4.5 / 24 / (2**23 - 1)
PACKET_LEN = 33

# 帧尾 = 0xC0 | packetType（取自固件 PACKET_TYPE 枚举）
FOOTER = {
    "zero": 0xC0,   # PACKET_TYPE_ACCEL，aux 字节填 0
    "aux":  0xC1,   # PACKET_TYPE_RAW_AUX
    "time": 0xC6,   # PACKET_TYPE_RAW_AUX_TIME_SYNC，后 4 字节是 32 位毫秒时间戳
}


# ═══════════════════════════════════════════════════════════
#  数据装载
# ═══════════════════════════════════════════════════════════
def load_gdf(subject, session, chans):
    """从原始 GDF 读连续数据（最贴近真实流式场景）"""
    import mne
    mne.set_log_level("ERROR")

    path = os.path.join(GDF_DIR, f"B{subject:02d}{session}.gdf")
    if not os.path.exists(path):
        raise SystemExit(f"找不到 {path}")
    raw = mne.io.read_raw_gdf(path, preload=True, verbose="ERROR")

    want = [c.upper().replace("EEG", "").replace(":", "").strip() for c in chans]
    pick, names = [], []
    for w in want:
        for ch in raw.ch_names:
            n = ch.upper().replace("EEG", "").replace(":", "").strip()
            if n == w:
                pick.append(ch); names.append(w); break

    data = raw.get_data(picks=pick) * 1e6          # V → µV
    print(f"  数据源: {os.path.basename(path)}  连续 {data.shape[1]} 点 "
          f"({data.shape[1]/FS:.1f} s)  通道 {names}")
    return data, names


def load_mat(subject, chans):
    """从 MATLAB 导出文件读（已试次化，这里按顺序拼成连续流）"""
    from scipy.io import loadmat
    path = os.path.join(MAT_DIR, f"subj{subject:02d}.mat")
    if not os.path.exists(path):
        raise SystemExit(f"找不到 {path}，请先跑 python/export_2b_to_mat.py")
    D = loadmat(path, squeeze_me=True, struct_as_record=False)
    meta = D["meta"]
    G = float(meta.G if hasattr(meta, "G") else meta["G"])

    segs, ys = [], []
    for key, lab in (("Xtr", "ytr"), ("Xte", "yte")):
        X = np.asarray(D[key], dtype=np.float64) / G
        segs.append(X); ys.append(np.asarray(D[lab]).ravel())
    X = np.concatenate(segs, axis=0)               # (n_trials, 3, T)
    y = np.concatenate(ys)
    n, nch, T = X.shape
    # 只在试次之间插 0.5 s 间隔，保持时序不"黏"在一起
    gap = np.zeros((n, nch, int(0.5 * FS)))
    stream = np.concatenate([np.concatenate([X[i], gap], axis=1) for i in range(n)], axis=1)
    print(f"  数据源: {os.path.basename(path)}  {n} 试次 → 连续 {stream.shape[1]} 点 "
          f"({stream.shape[1]/FS:.1f} s)")
    print(f"  （试次顺序: 左手 y=0 / 右手 y=1，试次间插 0.5 s 静息）")
    return stream, ["C3", "Cz", "C4"]


# ═══════════════════════════════════════════════════════════
#  打包
# ═══════════════════════════════════════════════════════════
def build_packet(sample_uv, seq, header, aux_mode, t_ms):
    """sample_uv: 长度 8 的 µV 数组 → 33 字节"""
    pkt = bytearray(PACKET_LEN)
    pkt[0] = header
    pkt[1] = seq & 0xFF

    counts = np.round(np.asarray(sample_uv, dtype=np.float64) / UV_PER_COUNT)
    counts = np.clip(counts, -8388608, 8388607).astype(np.int64)
    for c in range(8):
        v = int(counts[c]) & 0xFFFFFF               # 补码低 24 位
        pkt[2 + c*3 + 0] = (v >> 16) & 0xFF         # 大端，MSB 在前
        pkt[2 + c*3 + 1] = (v >> 8) & 0xFF
        pkt[2 + c*3 + 2] = v & 0xFF

    pkt[26:32] = b"\x00" * 6
    if aux_mode == "time":
        t = int(t_ms) & 0xFFFFFFFF
        pkt[26] = 0; pkt[27] = 0
        pkt[28] = (t >> 24) & 0xFF                  # T3
        pkt[29] = (t >> 16) & 0xFF                  # T2
        pkt[30] = (t >> 8) & 0xFF                   # T1
        pkt[31] = t & 0xFF                          # T0

    pkt[32] = FOOTER[aux_mode]
    return bytes(pkt)


def make_8ch(data, n_chan_real, dc_uv=0.0):
    """(n_ch, T) → (8, T)，不足的通道填 0；可选注入直流偏移"""
    n, T = data.shape
    out = np.zeros((8, T), dtype=np.float32)
    out[:min(n, 8), :] = data[:min(n, 8), :]
    if dc_uv:
        out[:n_chan_real, :] += dc_uv
    return out


# ═══════════════════════════════════════════════════════════
#  初始化串（收到 'v' 时回给主机，以 $$$ 结尾）
# ═══════════════════════════════════════════════════════════
INIT_TEXT = (
    "OpenBCI V3 8bit Board\r\n"
    "Setting ADS1299 Channel Values\r\n"
    "ADS1299 Device ID: 0x3E\r\n"
    "LIS3DH Device ID: 0x33\r\n"
    "Firmware: v3.1.1\r\n"
    "$$$\r\n"
).encode()


# ═══════════════════════════════════════════════════════════
#  输出通道（串口 / 文件 / TCP，统一接口）
# ═══════════════════════════════════════════════════════════
class Sink:
    def write(self, b): ...
    def flush(self): ...
    def close(self): ...
    def out_waiting(self): return 0
    def recv_cmd(self): return None      # 返回单个命令字符或 None


class SerialSink(Sink):
    def __init__(self, port, baud):
        import serial
        self.s = serial.Serial(port, baud, timeout=0)
        try:
            self.s.reset_input_buffer(); self.s.reset_output_buffer()
        except Exception:
            pass
        self.buf = b""

    def write(self, b): self.s.write(b)
    def flush(self): self.s.flush()
    def close(self): self.s.close()
    def out_waiting(self): return self.s.out_waiting

    def recv_cmd(self):
        try:
            n = self.s.in_waiting
            if n:
                self.buf += self.s.read(n)
        except Exception:
            return None
        if b"/1" in self.buf:
            self.buf = self.buf.replace(b"/1", b""); return "/1"
        if self.buf:
            c = self.buf[:1].decode(errors="ignore"); self.buf = self.buf[1:]
            return c if c in "vsb<" else None
        return None


class FileSink(Sink):
    """把原始字节流写入文件 —— 用于离线验证包格式，不需要任何硬件"""
    def __init__(self, path):
        self.f = open(path, "wb")
        self.n = 0

    def write(self, b): self.f.write(b); self.n += len(b)
    def flush(self): self.f.flush()
    def close(self): self.f.close()


class TcpSink(Sink):
    """监听 TCP 端口，等一个客户端连上后推送字节流（可用于 MODE_NET 降级路径）"""
    def __init__(self, port, timeout=30):
        import socket
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("0.0.0.0", port))
        self.srv.listen(1)
        self.srv.settimeout(timeout)
        print(f"  等待客户端连接 tcp://<本机IP>:{port} …")
        self.conn, addr = self.srv.accept()
        self.conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.conn.settimeout(0)
        print(f"  客户端已连接: {addr[0]}:{addr[1]}")

    def write(self, b):
        try:
            self.conn.sendall(b)
        except Exception:
            pass

    def flush(self): pass
    def close(self):
        try: self.conn.close(); self.srv.close()
        except Exception: pass

    def recv_cmd(self):
        try:
            d = self.conn.recv(64)
        except Exception:
            return None
        if d:
            for ch in d.decode(errors="ignore"):
                if ch in "vsb<":
                    return ch
        return None


def make_sink(spec, baud):
    if spec.startswith("file:"):
        p = spec[5:]
        print(f"  输出 → 文件 {p}")
        return FileSink(p), "文件"
    if spec.startswith("tcp:"):
        return TcpSink(int(spec[4:])), "TCP"
    return SerialSink(spec, baud), "串口"


# ═══════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(
        description="Cyton 串口模拟器（BCI IV 2b → 33 字节包 → CH340 → Zynq PS）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="列出串口后退出")
    ap.add_argument("--port", default=None, help="串口号，如 COM3")
    ap.add_argument("--sink", default=None,
                    help="输出目标：串口号 | file:路径 | tcp:端口。默认用 --port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--source", choices=["gdf", "mat", "bin"], default="gdf",
                    help="gdf=BCI IV 2b 连续数据；mat=试次拼接；bin=实采 .bin 原样回放")
    ap.add_argument("--bin", default=None, help="--source bin 的原始字节流文件")
    ap.add_argument("--rewrite-header", choices=["auto", "none", "41", "a0"],
                    default="auto",
                    help="bin 回放时改写首字节；auto=bin 默认改成 0x41（当前 PL 解析器只认 0x41）")
    ap.add_argument("--subject", type=int, default=1)
    ap.add_argument("--session", default="01T", help="GDF 会话：01T/02T/03T/04E/05E")
    ap.add_argument("--channels", default="C3,Cz,C4", help="映射到 Cyton 通道 1..N")
    ap.add_argument("--eog", action="store_true", help="把 EOG 也放进通道 4-6")
    ap.add_argument("--header", choices=["41", "a0"], default="41",
                    help="首字节：41=固件 OPENBCI_BOP（PIC32→RFduino），a0=dongle 输出格式")
    ap.add_argument("--aux", choices=list(FOOTER), default="zero")
    ap.add_argument("--dc-offset", type=float, default=0.0,
                    help="给真实通道注入直流偏移(µV)，用于测试去DC")
    ap.add_argument("--speed", type=float, default=1.0, help="播放倍速（受波特率限制）")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--no-wait", action="store_true", help="不等 'b' 命令，立即开始")
    ap.add_argument("--dry-run", action="store_true", help="只打印前几包，不发送")
    ap.add_argument("--n", type=int, default=8, help="dry-run 打印包数")
    a = ap.parse_args()

    if a.list:
        import serial.tools.list_ports as lp
        ps = list(lp.comports())
        print(f"发现 {len(ps)} 个串口:")
        for p in ps:
            print(f"  {p.device:8s} {p.description}")
        return

    # ── 装载数据 ──
    print("=" * 74)
    print("Cyton 串口模拟器")
    print("=" * 74)
    chans = [c.strip() for c in a.channels.split(",") if c.strip()]
    raw_packets = None
    stream = None
    if a.source == "bin":
        if not a.bin:
            raise SystemExit("--source bin 需要 --bin 指定 .bin 文件")
        with open(a.bin, "rb") as f:
            blob = f.read()
        parser = CytonParser(None)
        raw_packets = parser.feed(blob)
        if not raw_packets:
            raise SystemExit(f"{a.bin} 里没有解析出完整包（长度 {len(blob)} 字节）")
        rewrite = a.rewrite_header
        if rewrite == "auto":
            rewrite = "41"
        if rewrite == "none":
            print(f"  帧头: 原样（首包 0x{raw_packets[0][0]:02X}）")
        else:
            hb = 0x41 if rewrite == "41" else 0xA0
            changed = sum(1 for p in raw_packets if p[0] != hb)
            raw_packets = [bytes([hb]) + p[1:] for p in raw_packets]
            print(f"  帧头: 改写为 0x{hb:02X}（{changed}/{len(raw_packets)} 包）")
        print(f"  数据源: {os.path.basename(a.bin)}  {len(raw_packets)} 包 "
              f"({len(raw_packets)/FS:.1f} s)  重同步 {parser.n_resync} 次")
        n_total = len(raw_packets)
        if a.channels != "C3,Cz,C4" or a.dc_offset:
            print("  注: bin 按字节原样回放，--channels / --dc-offset 不生效")
    else:
        if a.source == "gdf":
            data, names = load_gdf(a.subject, a.session, chans)
        else:
            data, names = load_mat(a.subject, chans)
        stream = make_8ch(data, len(names), a.dc_offset)
        n_total = stream.shape[1]

    bw = 33 * 10 / a.baud * FS * a.speed
    hdr_desc = (f"0x{raw_packets[0][0]:02X}" if raw_packets is not None
                else f"0x{a.header.upper()}")
    foot_desc = (f"0x{raw_packets[0][32]:02X}" if raw_packets is not None
                 else f"0x{FOOTER[a.aux]:02X}")
    print(f"  输出: {8} 通道 × 24bit 大端 | 帧头 {hdr_desc} | "
          f"帧尾 {foot_desc} | {a.baud} 8N1")
    print(f"  速率: {FS*a.speed:.0f} Hz × 33 B = {33*FS*a.speed:.0f} B/s "
          f"→ 占用带宽 {bw*100:.1f}%")
    if bw > 0.95:
        print("  ⚠ 超过 95% 带宽，会丢时序。降 --speed 或提高 --baud")
    print(f"  时长: {n_total/FS/a.speed:.1f} s"
          f"{'（循环）' if a.loop else ''}")

    header = 0x41 if a.header == "41" else 0xA0

    # ── 干跑 ──
    if a.dry_run:
        print(f"\n干跑：前 {a.n} 包的十六进制")
        for i in range(min(a.n, n_total)):
            if raw_packets is not None:
                p = raw_packets[i]
            else:
                p = build_packet(stream[:, i], i, header, a.aux, i * 4)
            print(f"  #{i:3d} seq={p[1]:3d}  {p.hex(' ')}")
        c1 = int.from_bytes(p[2:5], "big", signed=False)
        if c1 & 0x800000:
            c1 -= 1 << 24
        print(f"\n  首字节 = 0x{p[0]:02X}  帧尾 = 0x{p[32]:02X}  包长 = {len(p)}")
        print(f"  通道1 原始计数 = {c1}  →  {c1 * UV_PER_COUNT:.2f} µV")
        return

    spec = a.sink or a.port
    if not spec:
        raise SystemExit("请用 --port 或 --sink 指定输出目标（--list 可查看串口）")
    sink, kind = make_sink(spec, a.baud)
    print(f"  输出: {kind} [{spec}]\n")

    # ── 握手 ──
    streaming = a.no_wait or kind == "文件"
    if not streaming:
        print("  等待 Zynq 侧命令…（发 'v' 复位 / 'b' 开始 / 's' 停止，Ctrl+C 退出）")
        while True:
            c = sink.recv_cmd()
            if c is None:
                time.sleep(0.005); continue
            if c == "v":
                sink.write(INIT_TEXT); sink.flush()
                print("    ← 收到 'v'，已回初始化串（以 $$$ 结尾）")
            elif c == "b":
                streaming = True
                print("    ← 收到 'b'，开始推流"); break
            elif c == "s":
                print("    ← 收到 's'，未开始，继续等待")

    # ── 推流 ──
    period = 1.0 / (FS * a.speed)
    state = {"seq": 0, "total": 0, "late_cnt": 0, "late_max": 0.0}
    next_t = time.perf_counter()
    t_start = time.perf_counter()
    t_wall0 = t_start

    def emit(i):
        if raw_packets is not None:
            pkt = raw_packets[i]
        else:
            pkt = build_packet(stream[:, i], state["seq"], header, a.aux,
                               (time.perf_counter() - t_wall0) * 1000.0)
        sink.write(pkt)
        state["seq"] = (state["seq"] + 1) & 0xFF
        state["total"] += 1
        # 精确节拍：先粗睡，再自旋
        nonlocal_next = next_t + period
        now = time.perf_counter()
        if now < nonlocal_next:
            slack = nonlocal_next - now
            if slack > 0.002:
                time.sleep(slack - 0.001)
            while time.perf_counter() < nonlocal_next:
                pass
        else:
            lateness = now - nonlocal_next
            state["late_cnt"] += 1
            state["late_max"] = max(state["late_max"], lateness)
            if lateness > period * 4:            # 落后超过 4 个采样周期，重新对齐
                return now
        return nonlocal_next

    print("  开始推流… Ctrl+C 停止\n")
    t_last = time.perf_counter()
    stop = False
    try:
        while not stop:
            for i in range(n_total):
                next_t = emit(i)
                c = sink.recv_cmd()
                if c == "s":
                    stop = True; break
                if c == "v":
                    sink.write(INIT_TEXT); sink.flush()
                now = time.perf_counter()
                if now - t_last >= 2.0:
                    el = now - t_start
                    extra = f"  缓冲 {sink.out_waiting()} B" if kind == "串口" else ""
                    print(f"    {state['total']:7d} 包  {el:6.1f} s  "
                          f"实测 {state['total']/el:7.2f} Hz{extra}  "
                          f"迟到 {state['late_cnt']} 次(最大 {state['late_max']*1000:.1f} ms)")
                    t_last = now
            if not a.loop:
                break
    except KeyboardInterrupt:
        pass

    el = time.perf_counter() - t_start
    sink.flush(); sink.close()
    print(f"\n  共发送 {state['total']} 包，用时 {el:.2f} s，"
          f"平均 {state['total']/el:.2f} Hz （目标 {FS*a.speed:.0f} Hz）")
    print(f"  迟到 {state['late_cnt']} 次，最大 {state['late_max']*1000:.2f} ms")
    if state["late_cnt"] == 0:
        print("  ✓ 时序完全达标")
    if kind == "文件":
        print(f"  字节流已写入 {spec[5:]}，用 cyton_parse.py 验证")


if __name__ == "__main__":
    main()
