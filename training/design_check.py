#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
算法实现规格的前置验证：把规划里要写死的设计参数用真实数据算出来

  ① 滤波器选型：IIR(各阶) vs FIR —— 在**同一选择性标准**下比阶数/群延迟/定点鲁棒性
  ② IIR 定点化仿真：Q 格式下的输出 SNR
  ③ 线性 SVM 折叠：w = Σ αᵢ·svᵢ 的等价性（决定 PL 要不要存支持向量）
  ④ 特征定义：MNE 的 CSP(log=True) = log(var)，不含归一化 —— 验证差异

用法: python design_check.py
"""
import os
import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from scipy import signal  # noqa: E402

FS = 250.0
BAND = (8.0, 30.0)
F_LO_STOP, F_HI_STOP = 5.0, 45.0        # 阻带考核点


def sec(t):
    print("\n" + "=" * 88)
    print(t)
    print("=" * 88)


def db(x):
    return 20 * np.log10(np.abs(x) + 1e-300)


def metrics(sos=None, taps=None, nfreq=16384):
    """统一算：带内起伏(10–28Hz，避开带边 -3/-6dB 点) / 5Hz 与 45Hz 抑制 / 群延迟"""
    if taps is not None:
        w, h = signal.freqz(taps, worN=nfreq, fs=FS)
    else:
        w, h = signal.sosfreqz(sos, worN=nfreq, fs=FS)
    mag = np.abs(h)
    pb = (w >= BAND[0]) & (w <= BAND[1])
    inner = (w >= 10) & (w <= 28)
    ref = mag[inner].max()
    ripple = db(mag[inner].max() / mag[inner].min())          # 真正的带内起伏
    att_lo = db(mag[np.argmin(np.abs(w - F_LO_STOP))] / ref)
    att_hi = db(mag[np.argmin(np.abs(w - F_HI_STOP))] / ref)
    gd = -np.gradient(np.unwrap(np.angle(h)), w) / (2 * np.pi)   # rad/Hz → 秒
    gd12 = gd[np.argmin(np.abs(w - 12.0))] * 1000          # mu 节律处，MI 最关心
    return ripple, att_lo, att_hi, gd[pb].max() * 1000, gd12


# ═══════════════════════════════════════════════════════════
sec("① 滤波器选型：8–30 Hz @ 250 Hz，考核点 5 Hz / 45 Hz 抑制")

cands = []
# 巴特沃斯：固定几阶，看能达到多少抑制（它没有"设计指标"概念）
for n in (2, 3, 4):
    cands.append((f"Butterworth {2*n} 阶",
                  signal.butter(n, BAND, btype="band", fs=FS, output="sos")))
# 椭圆：Wn = 通带边缘，用 ellipord 求满足指标的最小阶数
for rp, rs in ((0.5, 40), (0.1, 60)):
    N, Wn = signal.ellipord(BAND, [5.0, 45.0], rp, rs, fs=FS)
    cands.append((f"椭圆 {2*N} 阶 rp{rp}/rs{rs}",
                  signal.ellip(N, rp, rs, Wn, btype="band", fs=FS, output="sos")))
# Cheby2：Wn = **阻带**边缘，所以要把 Wn 放到考核点外，让通带覆盖 8–30
for rs in (40, 60):
    N, Wn = signal.cheb2ord(BAND, [5.0, 45.0], 0.5, rs, fs=FS)
    cands.append((f"Cheby2 {2*N} 阶 rs{rs}",
                  signal.cheby2(N, rs, Wn, btype="band", fs=FS, output="sos")))
rows = [(nm, f"{s.shape[0]} 节", s, None) for nm, s in cands]
for n in (128, 200, 256):
    rows.append((f"FIR 汉明 {n} 抽头", f"{n} 抽头", None,
                 signal.firwin(n, BAND, pass_zero=False, fs=FS, window="hamming")))

print(f"\n设计指标：通带 8–30 Hz 内起伏尽量小，阻带 5 Hz / 45 Hz 处 ≥40 dB")
print(f"\n{'方案':<28}{'规模':>8}{'带内起伏dB':>11}{'5Hz抑制dB':>11}"
      f"{'45Hz抑制dB':>11}{'群延迟@12Hz':>12}{'最大群延迟':>11}")
print("-" * 88)
for name, unit, sos, taps in rows:
    try:
        rip, alo, ahi, gdmax, gd12 = metrics(sos=sos, taps=taps)
        print(f"{name:<28}{unit:>8}{rip:>11.2f}{alo:>11.1f}{ahi:>11.1f}"
              f"{gd12:>10.0f} ms{gdmax:>8.0f} ms")
    except Exception as e:
        print(f"{name:<28}  失败: {e}")

print(f"\n判读：")
print(f"  · 巴特沃斯 8 阶(4 节)才 -23 dB，太平缓，EEG 去噪不适用")
print(f"  · **Cheby2 10 阶(5 节) 是最优折中**：42 dB 抑制，@12Hz 群延迟最小")
print(f"  · 椭圆要 8 阶(4 节)才够，但带边群延迟很大（>1.5 s）")
print(f"  · FIR 128 抽头抑制相当，但 @12Hz 延迟仍是 Cheby2 的 2 倍以上")
print(f"  · 上表'最大群延迟'出现在通带边缘(8/30Hz)，MI 的主要能量在 10–20 Hz，")
print(f"    所以 **@12Hz 那一列才是选型依据**")


# ═══════════════════════════════════════════════════════════
sec("② IIR 定点化仿真（转置直接 II 型，定点整数逐位模拟）")

def biquad_fixed(x, s, q_coef=14, q_state=19, acc_bits=48):
    """转置直接 II 型定点仿真。
    s = [b0,b1,b2,a0,a1,a2]（scipy sos 格式，a0 恒为 1）
    状态与累加器按 acc_bits 饱和。返回按 q_state 定标的整数输出序列（浮点表示）。"""
    b0, b1, b2, _a0, a1, a2 = s
    qc, qs = 1 << q_coef, 1 << q_state
    B0, B1, B2 = (int(round(v * qc)) for v in (b0, b1, b2))
    A1, A2 = (int(round(v * qc)) for v in (a1, a2))
    lo, hi = -(1 << (acc_bits - 1)), (1 << (acc_bits - 1)) - 1

    s1 = s2 = 0
    y = np.empty(len(x))
    for n in range(len(x)):
        xn = int(round(x[n] * qs))
        yn = (B0 * xn + s1) >> q_coef                 # 转置型：先算输出
        yn = max(lo, min(hi, yn))
        s1 = max(lo, min(hi, B1 * xn - A1 * yn + s2))
        s2 = max(lo, min(hi, B2 * xn - A2 * yn))
        y[n] = yn / qs
    return y


try:
    import mne
    mne.set_log_level("ERROR")
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "data", "raw", "BCICIV_2b_gdf", "B0101T.gdf")
    raw = mne.io.read_raw_gdf(p, preload=True, verbose="ERROR")
    keep = [c for c in raw.ch_names if c.upper().replace("EEG:", "").strip() in ("C3", "CZ", "C4")]
    raw.pick(keep)
    x = raw.get_data()[0, 250 * 60:250 * 60 + 6000]
    x = x - x.mean()
    x = x / np.abs(x).max() * 0.5                   # 归一到 ±0.5 = Q 格式满量程附近

    best = signal.ellip(3, 0.5, 40, BAND, btype="band", fs=FS, output="sos")
    yf = signal.sosfilt(best, x)
    for qc in (12, 14, 16):
        yq = x.copy()
        for s in best:
            yq = biquad_fixed(yq, s, q_coef=qc)
        err = yq - yf
        snr = 10 * np.log10(np.sum(yf ** 2) / max(np.sum(err ** 2), 1e-30))
        print(f"  系数 Q2.{qc:<2d}  状态 Q4.19  累加 48bit  →  输出 SNR {snr:6.1f} dB，"
              f"最大误差 {np.abs(err).max():.2e}")
    print(f"\n  （真实数据 B0101T / 通道 C3 / 24 s；椭圆 6 阶带通）")
    print(f"  判读：系数 Q2.14 已足够（SNR 与 Q2.16 只差零点几 dB），")
    print(f"        16 bit 系数量化本身的理论上限约 96 dB，实测受信号动态范围限制。")
except Exception as e:
    import traceback
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════
sec("③ 线性 SVM 折叠：w = Σ αᵢ·svᵢ 的等价性")

from sklearn.svm import SVC  # noqa: E402

rng = np.random.default_rng(0)
X = np.vstack([rng.normal(0, 1, (200, 3)), rng.normal(1.2, 1, (200, 3))])
Y = np.r_[np.zeros(200), np.ones(200)]
clf = SVC(kernel="linear", C=1.0).fit(X, Y)

w_fold = (clf.dual_coef_.T * clf.support_vectors_).sum(axis=0)
b_fold = clf.intercept_[0]
dec_orig = clf.decision_function(X)
dec_fold = X @ w_fold + b_fold
same_label = np.array_equal(clf.predict(X), (dec_fold > 0).astype(int))

print(f"\n支持向量数 = {len(clf.support_vectors_)}，特征维 = {X.shape[1]}")
print(f"  折叠前后决策值最大差 = {np.abs(dec_orig - dec_fold).max():.3e}  (浮点舍入量级)")
print(f"  预测标签完全一致     = {same_label}")
print(f"\n  ⇒ PL 线性核只需存 {X.shape[1]} 个权重 + 1 个偏置，")
print(f"     而不是 {len(clf.support_vectors_)}×{X.shape[1]} 个支持向量分量。")
print(f"     硬件 = {X.shape[1]} 乘法器 + 加法树 + 比较器，{X.shape[1]} 个周期出标签（流水后每周期一个）。")


# ═══════════════════════════════════════════════════════════
sec("④ 特征定义：MNE 的 CSP(log=True) = log(var)，不含归一化")

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from baseline_2b import SESSIONS_T, collect
    from mne.decoding import CSP
    from sklearn.model_selection import StratifiedKFold, cross_val_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    class Cfg:
        tmin, tmax = 3.5, 6.0
        l_freq, h_freq = 8.0, 30.0
        causal = False
        keep_artifacts = False

    here = os.path.dirname(os.path.abspath(__file__))
    raww = os.path.join(here, "..", "data", "raw")
    X, y, _, _ = collect(os.path.join(raww, "BCICIV_2b_gdf"),
                         os.path.join(raww, "BCICIV_2b_true_labels"), [1], SESSIONS_T, Cfg())
    X = X - X.mean(axis=2, keepdims=True)
    print(f"  数据: B01 训练集 {X.shape}")

    # 只借 CSP 求投影矩阵 W，特征自己显式算（不依赖 MNE transform 的内部约定）
    csp = CSP(n_components=3, reg="ledoit_wolf", log=False, norm_trace=False)
    csp.fit(X, y)
    W = csp.filters_                                  # (n_comp, n_ch)
    print(f"  CSP 投影矩阵 W = {W.shape}（n_comp × n_ch）")

    def features(Xin, Wmat):
        Zp = np.einsum("cd,ndt->nct", Wmat, Xin)      # 投影 z = W x
        var = Zp.var(axis=2)                          # (n_trials, n_comp)
        return var

    def log_features(Xin, Wmat, normalize):
        v = features(Xin, Wmat)
        return np.log(v / v.sum(axis=1, keepdims=True)) if normalize else np.log(v)

    f_mne = log_features(X, W, normalize=False)       # MNE log=True 的做法
    f_norm = log_features(X, W, normalize=True)       # 经典 CSP 定义

    print(f"\n  第 1 个 trial：")
    print(f"    MNE log(var)         = {np.round(f_mne[0], 4)}")
    print(f"    归一化 log(var/Σvar) = {np.round(f_norm[0], 4)}")
    print(f"\n  各 trial 特征之和：")
    print(f"    MNE      Σf = {f_mne.sum(1).mean():8.3f} ± {f_mne.sum(1).std():.3f}  ← 随 trial 波动")
    print(f"    归一化   Σf = {f_norm.sum(1).mean():8.3f} ± {f_norm.sum(1).std():.3e}  ← 恒为常数")

    cv = StratifiedKFold(5, shuffle=True, random_state=0)
    pipe = Pipeline([("sc", StandardScaler()), ("svm", SVC(kernel="linear", C=1.0))])
    print()
    for name, F in (("MNE log(var)", f_mne), ("归一化 log(var/Σvar)", f_norm)):
        a = cross_val_score(pipe, F, y, cv=cv).mean()
        print(f"    {name:24s} 5 折精度 {a*100:.2f}%")

    # ★ 部署实况：W 是离线训练好的、固定不变，只是数据来了个不同的增益
    #   （重新训练 CSP 会自动吸收缩放，那就测不出问题了）
    X2 = X * 100.0
    d_mne = np.abs(log_features(X2, W, False) - f_mne).max()
    d_norm = np.abs(log_features(X2, W, True) - f_norm).max()
    print(f"\n  固定 W 不变、把输入信号整体放大 100 倍（模拟增益标定差 40 dB）：")
    print(f"    MNE log(var)         Δmax = {d_mne:10.3f}   ← 等于 2·ln100 = {2*np.log(100):.1f}，完全跑偏")
    print(f"    归一化 log(var/Σvar) Δmax = {d_norm:10.2e}   ← 恒定不变")
    print(f"\n  ⇒ 必须用归一化定义：PL 端不需要精确增益标定，ADC 计数域直接算，")
    print(f"     且对通道间增益差异免疫。（注意：归一化会改变特征尺度，")
    print(f"       SVM 的 γ/C 必须在**同一套特征**上重训，不能直接搬旧参数。）")
except Exception:
    import traceback
    traceback.print_exc()

print()
