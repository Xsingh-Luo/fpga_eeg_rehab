# -*- coding: utf-8 -*-
"""pget — 多线程分块下载器（支持断点续传）

用法:
    python pget.py <url> <输出文件> [-t 线程数] [-k 分块大小MB]

原理: 利用 HTTP Range 请求把文件切成 N 块并行下载，
      每块写独立的 .partN 文件，全部完成后合并。
      单连接被限速时，多连接通常能成倍提速。
"""
import argparse
import os
import sys
import threading
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def head(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return int(r.headers["Content-Length"]), r.headers.get("Accept-Ranges", "")


def fetch_range(url, start, end, path, stats, idx, stop, retries=4):
    """下载 [start, end] 区间到 path，已存在则续传。stats[idx] 始终记录该块已落盘的总字节数"""
    total = end - start + 1
    for attempt in range(retries):
        have = os.path.getsize(path) if os.path.exists(path) else 0
        if have >= total:
            stats[idx] = total
            return True
        s = start + have
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": f"bytes={s}-{end}"})
        mode = "ab" if have else "wb"
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(path, mode) as f:
                while True:
                    if stop.is_set():
                        stats[idx] = os.path.getsize(path)
                        return False
                    b = r.read(262144)
                    if not b:
                        break
                    f.write(b)
                    stats[idx] = os.path.getsize(path)
        except Exception as e:
            if attempt == retries - 1:
                print(f"  [warn] chunk {idx} 重试 {retries} 次仍失败: {e}")
            else:
                time.sleep(2 * (attempt + 1))
            continue
        stats[idx] = os.path.getsize(path)
        if stats[idx] >= total:
            return True
    stats[idx] = os.path.getsize(path) if os.path.exists(path) else 0
    return stats[idx] >= total


def download(url, out, threads=16, chunk_mb=4):
    size, ar = head(url)
    print(f"[head] {size/1048576:.1f} MB   Accept-Ranges={ar!r}   threads={threads}")

    chunk = chunk_mb * 1024 * 1024
    ranges = [(i, min(i + chunk - 1, size - 1)) for i in range(0, size, chunk)]

    # 分块目录带分块大小：换了 -k 就用新目录，绝不与旧分块混用
    # （旧分块的字节偏移与新分块不同，混用会合并出损坏的文件）
    parts_dir = f"{out}.parts{chunk_mb}"
    os.makedirs(parts_dir, exist_ok=True)

    stats = [0] * len(ranges)
    stop = threading.Event()
    lock = threading.Lock()
    next_idx = [0]

    # 若已有合并好的完整文件，直接退出
    if os.path.exists(out) and os.path.getsize(out) == size:
        print("[skip] 目标文件已完整存在")
        return

    # 统计已落盘的分块字节数；过长的分块截断到应有长度（防历史遗留）
    for i, (a, b) in enumerate(ranges):
        p = os.path.join(parts_dir, f"{i:05d}.part")
        if os.path.exists(p):
            want = b - a + 1
            got = os.path.getsize(p)
            if got > want:
                with open(p, "r+b") as f:
                    f.truncate(want)
                got = want
            stats[i] = got

    t0 = time.time()
    last = [sum(stats)]

    def worker():
        while not stop.is_set():
            with lock:
                if next_idx[0] >= len(ranges):
                    return
                i = next_idx[0]
                next_idx[0] += 1
            a, b = ranges[i]
            p = os.path.join(parts_dir, f"{i:05d}.part")
            fetch_range(url, a, b, p, stats, i, stop)

    def reporter():
        while not stop.is_set():
            time.sleep(5)
            cur = sum(stats)
            el = time.time() - t0
            spd = (cur - last[0]) / 5 / 1024
            last[0] = cur
            pct = min(cur / size * 100, 100)
            eta = (size - cur) / max(spd * 1024, 1)
            print(f"  {pct:5.1f}%  {cur/1048576:7.1f}/{size/1048576:.1f} MB  "
                  f"{spd:8.0f} KB/s  ETA {eta/60:6.1f} min", flush=True)

    ths = [threading.Thread(target=worker, daemon=True) for _ in range(min(threads, len(ranges)))]
    rp = threading.Thread(target=reporter, daemon=True)
    rp.start()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    stop.set()

    # 检查完整性
    missing = []
    for i, (a, b) in enumerate(ranges):
        p = os.path.join(parts_dir, f"{i:05d}.part")
        want = b - a + 1
        got = os.path.getsize(p) if os.path.exists(p) else 0
        if got != want:
            missing.append((i, got, want))
    if missing:
        print(f"[fail] {len(missing)} 块不完整，重新运行本命令可续传")
        for i, g, w in missing[:5]:
            print(f"   chunk {i}: {g}/{w}")
        return False

    # 合并：每块只取应有的字节数
    print("[merge] 合并分块 ...")
    with open(out, "wb") as fo:
        for i, (a, b) in enumerate(ranges):
            p = os.path.join(parts_dir, f"{i:05d}.part")
            want = b - a + 1
            with open(p, "rb") as fi:
                left = want
                while left > 0:
                    chunk_data = fi.read(min(8 * 1024 * 1024, left))
                    if not chunk_data:
                        break
                    fo.write(chunk_data)
                    left -= len(chunk_data)
    got = os.path.getsize(out)
    el = time.time() - t0
    print(f"[done] {out}  {got/1048576:.1f} MB  用时 {el/60:.1f} min  平均 {got/1024/max(el,1):.0f} KB/s")
    if got == size:
        import shutil
        shutil.rmtree(parts_dir, ignore_errors=True)
        print("[done] 分块目录已清理")
        return True
    print("[fail] 大小不符")
    return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("-t", "--threads", type=int, default=16)
    ap.add_argument("-k", "--chunk-mb", type=int, default=4)
    a = ap.parse_args()
    ok = download(a.url, a.out, a.threads, a.chunk_mb)
    sys.exit(0 if ok else 1)
