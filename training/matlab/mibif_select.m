function [idx, mi] = mibif_select(F, y, k)
%MIBIF_SELECT 基于互信息的最优个体特征选择（Mutual Information Best Individual Feature）
%
%   [idx, mi] = mibif_select(F, y, k)
%
%   F   : (n_trials, n_feat) 特征
%   y   : (n_trials, 1) 标签
%   k   : 选前 k 个
%
%   idx : (k,1) 被选中的特征列索引（按互信息降序）
%   mi  : (1,n_feat) 各特征的互信息估计
%
%   这是 FBCSP 原始流程（Ang et al. 2012）里的特征选择步骤。
%   互信息用直方图法估计：对每个特征做 10 分位分箱，再算 MI。
%
%   ★ 必须在训练折内做，不能在全量数据上做——否则信息泄漏，精度会被高估。

n_feat = size(F, 2);
mi = zeros(1, n_feat);
for i = 1:n_feat
    mi(i) = mi_1d(F(:, i), y);
end
[~, ord] = sort(mi, 'descend');
idx = ord(1:min(k, n_feat));
idx = idx(:);
end


function v = mi_1d(f, y)
%MI_1D 一维连续特征与离散标签的互信息（直方图法，10 分位分箱）
    nb = 10;
    e = local_quantile(f(:), linspace(0, 1, nb + 1));
    e(1) = -inf;  e(end) = inf;
    e = unique(e);
    if numel(e) < 3          % 特征几乎常量
        v = 0;  return;
    end
    b = local_discretize(f(:), e);
    cls = unique(y);
    N = numel(y);
    v = 0;
    for bi = 1:numel(e)-1
        for ci = 1:numel(cls)
            pj = sum(b == bi & y == cls(ci)) / N;
            if pj > 0
                pb = sum(b == bi) / N;
                pc = sum(y == cls(ci)) / N;
                v = v + pj * log2(pj / (pb * pc));
            end
        end
    end
end


function q = local_quantile(x, p)
%LOCAL_QUANTILE 手写分位数（避免依赖 Statistics Toolbox）
    x = sort(x(:));
    n = numel(x);
    if n == 0, q = zeros(size(p)); return; end
    pos = p(:)' * (n - 1) + 1;
    lo = floor(pos);  hi = ceil(pos);
    lo = max(1, min(n, lo));  hi = max(1, min(n, hi));
    w = pos - lo;
    q = x(lo) .* (1 - w) + x(hi) .* w;
end


function b = local_discretize(x, edges)
%LOCAL_DISCRETIZE 手写分箱（避免依赖 Statistics Toolbox）
    b = zeros(numel(x), 1);
    for i = 1:numel(x)
        j = find(x(i) >= edges, 1, 'last');
        if isempty(j), j = 1; end
        b(i) = min(j, numel(edges) - 1);
    end
end
