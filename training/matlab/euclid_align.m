function [Xa, M, Rbar] = euclid_align(X, nCal)
%EUCLID_ALIGN 欧氏对齐（Euclidean Alignment）—— 跨 session 域适应
%
%   [Xa, M, Rbar] = euclid_align(X, nCal)
%
%   X    : (n_trials, n_ch, n_times) 已带通、已裁剪、已去均值的试次数据
%   nCal : 用于估计平均协方差的试次数（默认全部）。**只需 10 个试次即饱和**
%
%   Xa   : 对齐后的数据，同样形状
%   M    : 白化矩阵 (n_ch × n_ch)
%   Rbar : 平均协方差
%
%   原理：不同 session 的协方差矩阵整体缩放与旋转不同，导致 CSP 滤波器失配。
%         用本段数据自己的平均协方差白化，把所有域拉到同一参考系。
%
%       X̃ = R̄^(-1/2) · X,   R̄ = (1/n) Σᵢ XᵢXᵢᵀ / tr(XᵢXᵢᵀ)
%
%   ★ 对 FPGA 零成本：白化可以折叠进 CSP 投影
%       z = W̃ᵀX̃ = W̃ᵀMX = (MᵀW̃)ᵀX   ⟹  W_eff = Mᵀ·W̃
%     推理时仍是一次矩阵乘，PL 无需任何改动。
%
%   实测（BCI IV 2b，9 被试 cross 协议）：+2.71 个百分点，9/9 被试受益。
%   算术均值与黎曼均值效果完全相同，故此处用算术均值（更简单）。

if nargin < 2 || isempty(nCal)
    nCal = size(X, 1);
end
nCal = min(nCal, size(X, 1));
nCh = size(X, 2);

% ── 平均协方差（迹归一化，消除试次间幅度差）──
Rbar = zeros(nCh);
for i = 1:nCal
    Xi = squeeze(X(i, :, :));
    S = Xi * Xi.';
    Rbar = Rbar + S / max(trace(S), 1e-30);
end
Rbar = Rbar / nCal;
Rbar = (Rbar + Rbar.') / 2;          % 强制对称，避免数值不对称

% ── 白化矩阵 M = R̄^(-1/2) ──
[U, D] = eig(Rbar);
d = max(diag(D), 1e-12);
M = U * diag(d .^ -0.5) * U.';

% ── 应用到全部试次 ──
Xa = zeros(size(X));
for i = 1:size(X, 1)
    Xa(i, :, :) = M * squeeze(X(i, :, :));
end
end
