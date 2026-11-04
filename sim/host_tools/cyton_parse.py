#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Cyton 33 字节包解析器 —— 模拟器的验证工具，也是 Zynq 端的参考实现

三条用途
────────
1. 验证 cyton_sim.py 的输出（离线读字节流文件）
2. 作为 Zynq 端 C / Verilog 解析逻辑的参考实现（状态机与判据完全一致）
3. 把字节流转成 CSV，方便用 OpenBCI GUI 的习惯方式查看

包格式（见 cyton_sim.py 头部说明）
──────────────────────────────────
  0     1    帧头 0x41（固件 OPENBCI_BOP）或 0xA0（dongle 输出给 PC）
  1     1    采样序号
  2    24    8 通道 × 24 位有符号，大端
  26     6   AUX
  32     1   帧尾 0xC0|packetType

用法
────
  python cyton_parse.py --input file:stream.bin
  python cyton_parse.py --input COM10 --n 500 --csv out.csv
  python cyton_parse.py --input tcp:9000 --host 127.0.0.1
"""
import argparse
import os
import sys
import time

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PKT = 33
UV_PER_COUNT = 0.022352
VALID_FOOTER = {0xC0, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6}
CH_NAME = [f"ch{i+1}" for i in range(8)]


# ═══════════════════════════════════════════════════════════
#  核心解析：与 Zynq 端状态机逐条对应
# ═══════════════════════════════════════════════════════════
class CytonParser:
    """逐字节喂入，吐出完整的 33 字节包。

    状态机（Zynq 端照此实现）：
      1. 在缓冲区里找帧头（0x41 或 0xA0）
      2. 从帧头起凑够 33 字节
      3. 校验第 33 字节是否落在合法帧尾集合
      4. 合法 → 输出；不合法 → 帧头后移一字节重新同步
    """

    def __init__(self, header=None):
        self.headers = [header] if header else [0x41, 0xA0]
        self.buf = bytearray()
        self.n_pkt = 0
        self.n_resync = 0      # 重新同步次数（反映线路质量）
        self.n_byte = 0        # 收到的总字节数

    def feed(self, data):
        self.buf += data
        self.n_byte += len(data)
        out = []
        while True:
            # 找帧头
            idx = -1
            for h in self.headers:
                j = self.buf.find(bytes([h]))
                if j >= 0 and (idx < 0 or j < idx):
                    idx = j
            if idx < 0:
                # 没有帧头，保留最后 1 字节（可能是半个帧头）
                if len(self.buf) > 1:
                    self.n_resync += len(self.buf) - 1
                    del self.buf[:-1]
                break
            if idx > 0:
                self.n_resync += idx
                del self.buf[:idx]
            if len(self.buf) < PKT:
                break
            if self.buf[PKT - 1] in VALID_FOOTER:
                out.append(bytes(self.buf[:PKT]))
                del self.buf[:PKT]
                self.n_pkt += 1
            else:
                # 帧尾不对，帧头前移一位重新找
                self.n_resync += 1
                del self.buf[:1]
        return out


def decode(pkt):
    """33 字节 → (8 通道计数, seq, footer, aux)"""
    seq = pkt[1]
    raw = np.frombuffer(pkt[2:26], dtype=">i4") if False else None
    vals = np.empty(8, dtype=np.int64)
    for c in range(8):
        b = pkt[2 + c*3: 5 + c*3]
        v = (b[0] << 16) | (b[1] << 8) | b[2]
        if v & 0x800000:
            v -= 1 << 24
        vals[c] = v
    footer = pkt[32]
    aux = pkt[26:32]
    ts = None
    if footer in (0xC5, 0xC6):
        ts = int.from_bytes(aux[2:6], "big")
    return vals, seq, footer, aux, ts


# ═══════════════════════════════════════════════════════════
def open_source(spec, timeout=5.0):
    if spec.startswith("file:"):
        return open(spec[5:], "rb"), "file"
    if spec.startswith("tcp:"):
        import socket
        host, port = "127.0.0.1", int(spec[4:])
        s = socket.create_connection((host, port), timeout=timeout)
        return s, "tcp"
    import serial
    return serial.Serial(spec, 115200, timeout=timeout), "serial"


def read_some(src, kind, n=4096):
    try:
        if kind == "file":
            return src.read(n)
        if kind == "tcp":
            return src.recv(n)
        return src.read(n) or b""
    except Exception:
        return b""


def main():
    ap = argparse.ArgumentParser(description="Cyton 33 字节包解析器")
    ap.add_argument("--input", required=True, help="file:路径 | 串口号 | tcp:端口")
    ap.add_argument("--header", choices=["41", "a0", "auto"], default="auto")
    ap.add_argument("--n", type=int, default=0, help="最多解析多少包（0=全部）")
    ap.add_argument("--csv", default=None, help="导出 CSV")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    hdr = None if a.header == "auto" else (0x41 if a.header == "41" else 0xA0)
    p = CytonParser(hdr)

    src, kind = open_source(a.input)
    print("=" * 76)
    print(f"Cyton 包解析 | 输入 {a.input} ({kind}) | 帧头 "
          f"{'自动(0x41/0xA0)' if hdr is None else hex(hdr)}")
    print("=" * 76)

    pkts = []
    t0 = time.time()
    try:
        while True:
            d = read_some(src, kind)
            if not d:
                if kind in ("file", "tcp"):
                    break
                if time.time() - t0 > 2.0 and not pkts:
                    break
                continue
            got = p.feed(d)
            # 实时输入（串口/TCP）只显示摘要；文件输入全收
            if a.quiet or kind != "file":
                pkts.extend(got)
                if pkts and not a.quiet and len(pkts) % 500 < len(got):
                    el = time.time() - t0
                    print(f"  {len(pkts):7d} 包  {el:6.1f} s  →  {len(pkts)/el:7.2f} Hz")
            else:
                pkts.extend(got)
            if a.n and len(pkts) >= a.n:
                break
            if kind == "file" and not d:
                break
    except KeyboardInterrupt:
        pass
    finally:
        try: src.close()
        except Exception: pass

    el = time.time() - t0
    if not pkts:
        print("\n✗ 没有解析出任何完整包。可能原因：")
        print("   · 串口打开失败或没数据（先确认 --input 正确）")
        print("   · 波特率不对")
        print(f"   · 收到 {p.n_byte} 字节但无合法包（帧头/帧尾不匹配）")
        sys.exit(1)

    P = np.array([decode(x)[0] for x in pkts])          # (n, 8)
    seqs = np.array([x[1] for x in pkts])
    foot = np.array([x[32] for x in pkts])
    tss = [decode(x)[4] for x in pkts]

    print(f"\n解析结果")
    print(f"  完整包     : {len(pkts)}")
    print(f"  收到字节   : {p.n_byte}  →  有效率 {len(pkts)*PKT/max(p.n_byte,1)*100:.2f}%")
    print(f"  重新同步   : {p.n_resync} 次")
    print(f"  帧尾分布   : " + ", ".join(f"0x{k:02X}×{int((foot==k).sum())}"
                                          for k in np.unique(foot)))
    # 序号连续性
    if len(seqs) > 1:
        d = np.diff(seqs.astype(int)) % 256
        drop = int((d != 1).sum())
        lost = int(((d - 1) % 256).sum())
        print(f"  序号连续性 : {'✓ 无丢包' if drop == 0 else f'✗ {drop} 处跳变，累计丢 {lost} 个样本'}")
    if kind != "file" and el > 0:
        print(f"  实测速率   : {len(pkts)/el:.2f} Hz （目标 250 Hz）")

    print(f"\n  各通道（计数 / µV）:")
    print(f"    {'通道':<6}{'均值':>12}{'标准差':>12}{'最小':>12}{'最大':>12}{'µV标准差':>12}")
    for c in range(8):
        v = P[:, c]
        if np.all(v == 0):
            print(f"    {CH_NAME[c]:<6}{'— 全 0（未接电极）':>50}")
        else:
            print(f"    {CH_NAME[c]:<6}{v.mean():12.1f}{v.std():12.1f}"
                  f"{v.min():12d}{v.max():12d}{v.std()*UV_PER_COUNT:12.2f}")

    if any(t is not None for t in tss):
        tsv = [t for t in tss if t is not None]
        if len(tsv) > 2:
            dt = np.diff(tsv)
            print(f"\n  时间戳: {tsv[0]} → {tsv[-1]} ms，"
                  f"相邻间隔 中位数 {np.median(dt):.0f} ms，"
                  f"抖动 {np.std(dt):.2f} ms（理想 4 ms）")

    if a.csv:
        with open(a.csv, "w", encoding="utf-8-sig") as f:
            f.write("sample," + ",".join(f"{c}_count" for c in CH_NAME) + ","
                    + ",".join(f"{c}_uV" for c in CH_NAME) + ",footer\n")
            for i, x in enumerate(pkts):
                v, sq, ft, _, _ = decode(x)
                f.write(f"{sq}," + ",".join(str(int(t)) for t in v) + ","
                        + ",".join(f"{t*UV_PER_COUNT:.4f}" for t in v)
                        + f",0x{ft:02X}\n")
        print(f"\n  CSV 已写入 {a.csv}")


if __name__ == "__main__":
    main()
