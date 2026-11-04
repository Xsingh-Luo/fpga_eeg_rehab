function F = csp_features(X, W, mode, q)
%CSP_FEATURES 归一化对数方差特征 —— CSP 的判别特征
%
%   F = csp_features(X, W)             浮点
%   F = csp_features(X, W, 'fixed', q) 定点（逐位模拟 PL 的整数运算）
%
%   X : (n_trials, n_ch, n_times)
%   W : (n_ch, n_comp) 空间滤波器
%   q : 定标结构体，含 .zf（z 小数位，默认 13）、.ff（特征小数位，默认 11）
%
%   F : (n_trials, n_comp)
%
%   ★ 特征定义必须用**归一化**版本：
%        f_i = log( var(z_i) / sum_j var(z_j) )
%     而不是 MNE 默认的 log(var(z_i))。
%
%   实测（design_check.py，把输入整体放大 100 倍）：
%        MNE log(var)          特征平移 9.210  ← 完全跑偏
%        归一化 log(var/Σvar)  特征变化 4e-16  ← 恒定不变
%   归一化让 PL 端不需要精确增益标定，且对通道间增益差异免疫。

if nargin < 3 || isempty(mode), mode = 'float'; end
if nargin < 4, q = struct(); end
if ~isfield(q, 'zf'), q.zf = 13; end
if ~isfield(q, 'ff'), q.ff = 11; end

n_trials = size(X, 1);
n_comp   = size(W, 2);

if strcmpi(mode, 'float')
    F = zeros(n_trials, n_comp);
    for i = 1:n_trials
        Xi = squeeze(X(i, :, :));          % (n_ch, n_times)
        Z  = W.' * Xi;                     % (n_comp, n_times)   z = W^T x
        v  = var(Z, 0, 2);                 % 每行方差
        v  = max(v, realmin);
        F(i, :) = log(v / sum(v)).';
    end
else
    % ── 定点：CSP 投影 → 平方累加 → 归一化 → log2 ──
    QZ  = 2^q.zf;
    QF  = 2^q.ff;
    Wq  = round(W * QZ);                   % 与 z 同域，Q?.zf
    F   = zeros(n_trials, n_comp);
    for i = 1:n_trials
        Xi = round(squeeze(X(i, :, :)));   % 输入已是整数计数域
        Zq = Wq.' * Xi;                    % 整数累加，无舍入
        Zq = double(Zq);
        s2 = sum(Zq.^2, 2);                % Σz²   （整数，零漂移）
        s1 = sum(Zq, 2);                   % Σz
        T  = size(Zq, 2);
        v  = (s2 - (s1.^2)/T) / T;         % 方差，整数域
        v  = max(v, 1);
        r  = v / sum(v);                    % 归一化比值，Σr = 1
        F(i, :) = (log2(r(:)).' * log(2) * QF);   % 自然对数域，Q?.ff
    end
    F = F / QF;
end
end
