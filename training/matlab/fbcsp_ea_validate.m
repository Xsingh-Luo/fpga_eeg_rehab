%% 最终配置验证 —— 3 子带 FBCSP + 欧氏对齐 + 线性 SVM
%
%  这是本仓库的**权威验证脚本**，替代早先的 fbcsp_nbpw_validate.m。
%
%  最终配置（由 python/optimize_2b.py 与 optimize_2b_v2.py 在 9 被试上扫出）：
%      3 子带 FBCSP：8-16 / 16-24 / 24-32 Hz，4 阶巴特沃斯（2 个二阶段/带）
%      欧氏对齐：10 个校准试次，算术均值，白化折进 CSP
%      CSP m=1 → n_comp = min(2, N) = 2
%      归一化对数方差 → 3 带 × 2 = 6 维 → MIBIF 选 4 维
%      线性 SVM
%
%  用法: matlab -batch "cd('H:\FPGA2026QS\matlab'); fbcsp_ea_validate"

clear; clc;
fprintf('====================================================================\n');
fprintf(' 最终配置验证  |  3 子带 FBCSP + 欧氏对齐 + 线性 SVM\n');
fprintf(' BCI Competition IV 2b  |  cross 协议 (01T+02T+03T → 04E+05E)\n');
fprintf('====================================================================\n');

%% ── 配置 ────────────────────────────────────────────────────────
cfg = struct();
cfg.dataDir   = fullfile(fileparts(mfilename('fullpath')), 'data');
cfg.subjects  = 1:9;
cfg.m         = 1;                 % CSP 每侧对数 → n_comp = min(2, N) = 2
cfg.kSelect   = 4;                 % MIBIF 选前 k 个
cfg.C         = 1.0;
cfg.causal    = false;             % false = 零相位（Python 管线用这个）；true = 因果（在线部署）
cfg.tUse      = [3.5 6.0];
cfg.nCalList  = [5 10 20 40 320];  % EA 校准试次数（320 = 全部）

F = load(fullfile(cfg.dataDir, 'fbcsp_filters.mat'));
SOS3 = arrayfun(@(i) squeeze(F.sos3(i,:,:)), 1:size(F.sos3,1), 'uni', 0);
fprintf('子带: %d 个   m=%d   MIBIF k=%d   因果滤波=%d   窗 [%.1f %.1f]s\n\n', ...
        numel(SOS3), cfg.m, cfg.kSelect, cfg.causal, cfg.tUse(1), cfg.tUse(2));

%% ── 主循环 ──────────────────────────────────────────────────────
nCalList = cfg.nCalList;
nC = numel(nCalList);
subs = cfg.subjects(:)';
nS = numel(subs);
ACC = nan(nS, nC + 1);             % 第 1 列 = 无 EA，其余 = 各校准量

fprintf('%-6s %-10s', '被试', '无EA');
for c = 1:nC
    fprintf('%9s', sprintf('EA-%s', mat2str(nCalList(c))));
end
fprintf('\n%s\n', repmat('-', 1, 16 + 11*nC));

for si = 1:nS
    s = subs(si);
    f = fullfile(cfg.dataDir, sprintf('subj%02d.mat', s));
    if ~isfile(f), continue; end
    D = load(f);
    meta = D.meta;  ytr = double(D.ytr(:));  yte = double(D.yte(:));

    Btr = prep_bands(D.Xtr, meta, SOS3, cfg);
    Bte = prep_bands(D.Xte, meta, SOS3, cfg);

    % ── 无 EA 基线 ──
    ACC(si, 1) = eval_acc(Btr, Bte, ytr, yte, cfg, [], []);
    fprintf('B%02d    %7.2f%%  ', s, ACC(si, 1) * 100);

    % ── 各校准量下的 EA ──
    for c = 1:nC
        [BtA, BtB] = align_bands(Btr, Bte, nCalList(c));
        ACC(si, c + 1) = eval_acc(BtA, BtB, ytr, yte, cfg, [], []);
        fprintf('%8.2f%%', ACC(si, c + 1) * 100);
    end
    fprintf('\n');
end

%% ── 汇总 ────────────────────────────────────────────────────────
fprintf('\n%s\n', repmat('=', 1, 16 + 11*nC));
m = mean(ACC, 1, 'omitnan');
fprintf('%-6s %7.2f%%  ', '平均', m(1) * 100);
fprintf('%8.2f%%', m(2:end) * 100);
fprintf('\n%-6s %+7.3f   ', 'kappa', 2*m(1)-1);
fprintf('%+8.3f', 2*m(2:end) - 1);
fprintf('\n%-6s %-10s', '', '(基线)');
for c = 1:nC
    fprintf('%+8.2f ', (m(c+1) - m(1)) * 100);
end
fprintf('  ← 相对无 EA 的增益(pp)\n');
fprintf('%s\n', repmat('-', 1, 16 + 11*nC));
fprintf('对照: BCI IV 2b 官方冠军 (2008, I2R FBCSP+NBPW)  kappa +0.600\n');

save(fullfile(cfg.dataDir, 'final_results.mat'), 'ACC', 'cfg');
fprintf('\n结果已存 final_results.mat\n');

%% ══════════════════════════════════════════════════════════════════
%  局部函数
%% ══════════════════════════════════════════════════════════════════

function Xb = prep_bands(Xraw, meta, sosCell, cfg)
%PREP_BANDS 去DC（共通）→ 逐带带通 → 裁剪 → 每 trial 去均值
    n = size(Xraw, 1);
    fs = meta.fs;  G = meta.G;
    X = double(Xraw) / G;
    a = 1 / (1 + 2*pi*0.5/fs);
    X = filter([a -a], [1 -a], X, [], 3);

    i0 = round((cfg.tUse(1) - meta.win_export(1)) * fs) + 1;
    i1 = i0 + round(diff(cfg.tUse) * fs);

    B = numel(sosCell);
    Xb = zeros(n, size(X,2), i1 - i0 + 1, B);
    for b = 1:B
        Xf = zeros(size(X));
        for i = 1:n
            Xi = squeeze(X(i, :, :)).';
            Xi = biquad_cascade(Xi, sosCell{b}, 'float');
            if ~cfg.causal
                Xi = flipud(biquad_cascade(flipud(Xi), sosCell{b}, 'float'));
            end
            Xf(i, :, :) = Xi.';
        end
        Xc = Xf(:, :, i0:i1);
        Xb(:, :, :, b) = Xc - mean(Xc, 3);
    end
end


function [A, B] = align_bands(Btr, Bte, nCal)
%ALIGN_BANDS 对每个子带独立做欧氏对齐（训练域用全部，测试域只用前 nCal 个估 R̄）
    nb = size(Btr, 4);
    A = zeros(size(Btr));  B = zeros(size(Bte));
    for b = 1:nb
        [A(:,:,:,b), ~] = euclid_align(Btr(:, :, :, b));           % 训练域：全部数据
        [B(:,:,:,b), ~] = euclid_align(Bte(:, :, :, b), nCal);     % 测试域：只用校准量
    end
end


function acc = eval_acc(Btr, Bte, ytr, yte, cfg, ~, ~)
%EVAL_ACC 逐带 CSP（只用训练集拟合）→ MIBIF（只用训练集）→ 线性 SVM
    nb = size(Btr, 4);
    Ftr = [];  Fte = [];
    for b = 1:nb
        W = csp_train(Btr(:, :, :, b), ytr, cfg.m, 'lw');
        Ftr = [Ftr, csp_features(Btr(:, :, :, b), W)];  %#ok<AGROW>
        Fte = [Fte, csp_features(Bte(:, :, :, b), W)];  %#ok<AGROW>
    end
    idx = mibif_select(Ftr, ytr, cfg.kSelect);
    Ftr = Ftr(:, idx);  Fte = Fte(:, idx);

    mu = mean(Ftr, 1);  sd = std(Ftr, 0, 1);  sd(sd < eps) = 1;
    Ztr = (Ftr - mu) ./ sd;   Zte = (Fte - mu) ./ sd;
    mdl = fitcsvm(Ztr, ytr, 'KernelFunction', 'linear', ...
                  'BoxConstraint', cfg.C, 'Standardize', false);
    acc = mean(double(predict(mdl, Zte)) == yte);
end
