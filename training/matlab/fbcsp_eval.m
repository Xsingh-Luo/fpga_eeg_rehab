function [acc, info] = fbcsp_eval(Btr, Bte, ytr, yte, cfg)
%FBCSP_EVAL 多子带 CSP 特征 + MIBIF 选择 + 线性 SVM，返回测试准确率
%
%   Btr, Bte : (n_trials, n_ch, T_win, B) 已对齐（或未对齐）的多子带数据
%   ytr, yte : 标签
%   cfg      : 需含 m（CSP 每侧对数）、kSelect（MIBIF 选前 k）、C（SVM 盒约束）
%
%   ★ 三道防泄漏：CSP 拟合、MIBIF 选择、特征标准化全部只用训练集统计量。

nb = size(Btr, 4);
Ftr = [];  Fte = [];
for b = 1:nb
    W = csp_train(Btr(:, :, :, b), ytr, cfg.m, 'lw');       % ★ 只用训练集
    Ftr = [Ftr, csp_features(Btr(:, :, :, b), W)];  %#ok<AGROW>
    Fte = [Fte, csp_features(Bte(:, :, :, b), W)];  %#ok<AGROW>
end

idx = mibif_select(Ftr, ytr, cfg.kSelect);                   % ★ 只用训练集
nFeatAll = size(Ftr, 2);
Ftr = Ftr(:, idx);  Fte = Fte(:, idx);

mu = mean(Ftr, 1);  sd = std(Ftr, 0, 1);  sd(sd < eps) = 1;  % ★ 只用训练集
Ztr = (Ftr - mu) ./ sd;
Zte = (Fte - mu) ./ sd;

mdl = fitcsvm(Ztr, ytr, 'KernelFunction', 'linear', ...
              'BoxConstraint', cfg.C, 'Standardize', false);
acc = mean(double(predict(mdl, Zte)) == yte);

info = struct('nBand', nb, 'nFeatAll', nFeatAll, ...
              'k', numel(idx), 'selIdx', idx);
end
