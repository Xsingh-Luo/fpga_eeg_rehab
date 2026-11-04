function [yhat, logp] = nbpw_predict(Xtr, ytr, Xte, sigma)
%NBPW_PREDICT 朴素贝叶斯 Parzen 窗分类器（Naive Bayes Parzen Window）
%
%   [yhat, logp] = nbpw_predict(Xtr, ytr, Xte, sigma)
%
%   Xtr   : (n_train, n_feat) 训练特征（**必须已标准化**）
%   ytr   : (n_train, 1) 标签
%   Xte   : (n_test, n_feat) 测试特征（用训练集统计量标准化）
%   sigma : Parzen 核宽（标准化后建议 0.2~2）
%
%   yhat  : (n_test,1) 预测标签
%   logp  : (n_test, n_class) 对数后验
%
%   这是 BCI Competition IV Dataset 2b 冠军方案（Chin et al., I2R Singapore）
%   用的分类器，配合 FBCSP 特征取得 kappa 0.60。
%
%   模型（朴素贝叶斯假设：特征维间条件独立）：
%       P(x|c) = Π_d  (1/Nc) Σ_{i∈c} K( (x_d − x_i,d) / σ )
%       K 为高斯核，σ 为核宽
%       判据：argmax_c  P(x|c)·P(c)
%
%   与 SVM 的区别：SVM 只关心边界（支持向量），NBPW 用**全部训练样本**
%   做密度估计，是生成式方法，小样本下往往更稳。

cls = unique(ytr);
nC = numel(cls);
nD = size(Xte, 2);
nTr = numel(ytr);

logp = zeros(size(Xte, 1), nC);
const = -0.5 * log(2 * pi) - log(sigma);        % 高斯核归一化常数（对数域）

for c = 1:nC
    Xc = Xtr(ytr == cls(c), :);
    nCc = size(Xc, 1);
    acc = zeros(size(Xte, 1), 1);
    for d = 1:nD
        % (n_test × n_train) 核矩阵
        K = exp(-0.5 * ((Xte(:, d) - Xc(:, d)') / sigma).^2) .* exp(const);
        pd = mean(K, 2);                        % Parzen 密度估计
        acc = acc + log(pd + realmin);
    end
    logp(:, c) = acc + log(nCc / nTr);          % 加上类先验
end

[~, idx] = max(logp, [], 2);
yhat = cls(idx);
end
