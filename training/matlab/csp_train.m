function [W, info] = csp_train(X, y, m, reg)
%CSP_TRAIN 共空间模式（CSP）空间滤波器训练 —— 只在 PC 上做，PL 不做特征分解
%
%   [W, info] = csp_train(X, y, m, reg)
%
%   X   : (n_trials, n_ch, n_times) 已去均值的多试次 EEG
%   y   : (n_trials, 1) 标签，0 / 1
%   m   : 每侧保留的滤波器对数
%   reg : 'lw'（Ledoit-Wolf 收缩，默认）| 'fixed'（固定 alpha=0.05）
%
%   W   : (n_ch, n_comp) 空间滤波器矩阵，n_comp = min(2m, n_ch)
%   info: 结构体，含 n_comp / 特征值 / 收缩系数
%
%   算法（对应 规划/02_算法实现规格书.md §2.4）：
%     ① 每 trial 去均值
%     ② R_c = X X^T / trace(X X^T)          迹归一化，消除 trial 间幅度差
%     ③ C1, C2 = 类内平均
%     ④ 正则化（收缩到 scaled identity）
%     ⑤ Cc = C1 + C2
%     ⑥ 白化 P = U D^(-1/2) U^T,  [U,D] = eig(Cc)
%     ⑦ S1 = P C1 P^T
%     ⑧ [V,L] = eig(S1)，按特征值降序
%     ⑨ W = P^T V
%     ⑩ 取首 m 列（类1方差大）与末 m 列（类2方差大）
%        ★ n_comp = min(2m, n_ch)：3 通道下 m=2 只得 3 个分量，不是 4

if nargin < 4 || isempty(reg), reg = 'lw'; end

[n_trials, n_ch, n_times] = size(X);
assert(numel(y) == n_trials, 'CSP: 标签数与试次数不一致');

% ① 每 trial 去均值
Xc = X - mean(X, 3);

% ② 单 trial 协方差（迹归一化）
R = zeros(n_ch, n_ch, n_trials);
for i = 1:n_trials
    Xi = squeeze(Xc(i, :, :));
    S  = Xi * Xi.';
    R(:, :, i) = S / trace(S);
end

% ③④ 类内平均 + 正则化
C = zeros(n_ch, n_ch, 2);
shrink = zeros(1, 2);
for c = 0:1
    idx = (y(:) == c);
    if strcmpi(reg, 'lw')
        [C(:, :, c+1), shrink(c+1)] = cov_shrink_lw(R(:, :, idx));
    else
        alpha = 0.05;
        Cc_ = mean(R(:, :, idx), 3);
        C(:, :, c+1) = (1-alpha)*Cc_ + alpha*(trace(Cc_)/n_ch)*eye(n_ch);
        shrink(c+1) = alpha;
    end
end
C1 = C(:, :, 1);  C2 = C(:, :, 2);

% ⑤ 复合协方差
Cc = C1 + C2;

% ⑥ 白化
[U, D] = eig((Cc + Cc.')/2);              % 对称化，避免数值不对称
d = diag(D);  d(d < eps) = eps;
P = U * diag(1./sqrt(d)) * U.';

% ⑦⑧ 白化后的类协方差 + 特征分解
S1 = P * C1 * P.';
S1 = (S1 + S1.')/2;
[V, L] = eig(S1);
[ev, ord] = sort(diag(L), 'descend');
V = V(:, ord);

% ⑨ 空间滤波器
Wall = P.' * V;                            % (n_ch, n_ch)

% ⑩ 取首末各 m 列
if 2*m <= n_ch
    sel = [1:m, n_ch-m+1:n_ch];
else
    sel = 1:n_ch;                          % 通道数不足，只能用全部
end
W = Wall(:, sel);
n_comp = numel(sel);

info = struct('n_comp', n_comp, 'eigvals', ev, 'shrink', shrink, ...
              'n_ch', n_ch, 'm', m, 'reg', reg, ...
              'note', sprintf('m=%d 请求 %d 个分量，实得 %d（受 %d 通道限制）', ...
                              m, 2*m, n_comp, n_ch));
end


function [C, s] = cov_shrink_lw(R)
%COV_SHRINK_LW Ledoit-Wolf 收缩估计（目标 = scaled identity）
%   R : (p, p, n) 每个试次的协方差矩阵
%   返回收缩后的平均协方差 C 与收缩强度 s（0=不收缩，1=完全收缩）
    [p, ~, n] = size(R);
    S  = mean(R, 3);
    mu = trace(S) / p;

    d2 = norm(S - mu*eye(p), 'fro')^2 / p;          % S 与目标的距离
    bb = 0;
    for i = 1:n
        bb = bb + norm(R(:, :, i) - S, 'fro')^2;
    end
    b2 = min(bb / (n^2) / p, d2);                    % 估计量的方差
    s  = b2 / d2;
    C  = (1 - s)*S + s*mu*eye(p);
end
