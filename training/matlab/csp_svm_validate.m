%% CSP + SVM 运动想象二分类 —— MATLAB 验证脚本
%
%  数据 : BCI Competition IV 2b（由 python/export_2b_to_mat.py 导出为 int16 计数域）
%  目标 : ① 用 MATLAB 独立复现 Python 基线，交叉验证算法链的正确性
%         ② 验证定点实现与浮点的差距
%         ③ 导出可下发给 FPGA 的参数
%
%  对应文档 : 规划/02_算法实现规格书.md
%
%  用法 : 在 matlab/ 目录下直接运行本脚本，或
%         matlab -batch "cd('H:\FPGA2026QS\matlab'); csp_svm_validate"
%
%  输出 : results_*.mat / 控制台表格

clear; clc;
fprintf('==============================================================\n');
fprintf(' CSP + SVM 验证  |  BCI Competition IV 2b\n');
fprintf('==============================================================\n');

%% ── 配置 ────────────────────────────────────────────────────────
cfg = struct();
cfg.dataDir   = fullfile(fileparts(mfilename('fullpath')), 'data');
cfg.subjects  = 1:9;
cfg.m         = 2;            % 每侧 CSP 滤波器对数
cfg.C         = 1.0;          % SVM 盒约束
cfg.kernel    = 'linear';     % 主线；'rbf' 作对照
cfg.reg       = 'lw';         % 'lw' = Ledoit-Wolf 收缩 | 'fixed' = alpha 0.05
cfg.causal    = true;         % true = 单向滤波（对应在线部署），false = 零相位
cfg.tUse      = [3.5, 6.0];   % 裁剪到该窗（相对试次起点 768），2.5 s = 626 点
cfg.cvFolds   = 10;
cfg.cvRepeats = 3;            % Python 基线用 10；这里取 3 以控制运行时间
cfg.exportSubj = 1;           % 用哪个受试者导出 FPGA 参数
rng(42);

sos = cheby2_bandpass_sos();
fprintf('滤波器: Cheby2 10 阶带通 8-30 Hz, %d 个二阶段, 模式=%s\n', ...
        size(sos,1), ternary(cfg.causal,'因果(在线)','零相位'));
fprintf('CSP m=%d | SVM %s C=%.1f | 正则化 %s | 窗 [%.1f, %.1f] s\n\n', ...
        cfg.m, cfg.kernel, cfg.C, cfg.reg, cfg.tUse(1), cfg.tUse(2));

%% ── 逐被试验证 ──────────────────────────────────────────────────
R = struct('subj', {}, 'accWithin', {}, 'kapWithin', {}, ...
           'accCross', {}, 'kapCross', {}, 'nComp', {}, ...
           'accFixed', {}, 'featErr', {});

for s = cfg.subjects
    f = fullfile(cfg.dataDir, sprintf('subj%02d.mat', s));
    if ~isfile(f), fprintf('  subj%02d 缺失，跳过\n', s); continue; end
    D = load(f);

    % ── 预处理 ──
    [Xtr, ~] = preprocess(D.Xtr, D.meta, sos, cfg);
    [Xte, ~] = preprocess(D.Xte, D.meta, sos, cfg);
    ytr = double(D.ytr(:));  yte = double(D.yte(:));

    % ── 协议 A：训练集内重复分层交叉验证 ──
    [accW, ~] = cv_within(Xtr, ytr, cfg);

    % ── 协议 B：训练集 → 评价集（官方划分）──
    [W, info] = csp_train(Xtr, ytr, cfg.m, cfg.reg);
    Ftr = csp_features(Xtr, W);
    Fte = csp_features(Xte, W);
    [mdl, wfold, bfold] = fit_svm(Ftr, ytr, cfg);
    accC = mean(predict_labels(Fte, wfold, bfold) == yte);

    % ── 定点 vs 浮点 ──
    Ftr_fx = csp_features(Xtr, W, 'fixed');
    Fte_fx = csp_features(Xte, W, 'fixed');
    mdl_fx = fitcsvm(Ftr_fx, ytr, 'KernelFunction', cfg.kernel, ...
                     'BoxConstraint', cfg.C, 'Standardize', false);
    accFx  = mean(double(predict(mdl_fx, Fte_fx)) == yte);
    relErr = max(abs(Ftr_fx(:) - Ftr(:)) ./ max(abs(Ftr(:)), 1e-6));

    R(end+1) = struct('subj', s, ...
        'accWithin', accW, 'kapWithin', 2*accW-1, ...
        'accCross', accC, 'kapCross', 2*accC-1, ...
        'nComp', info.n_comp, 'accFixed', accFx, 'featErr', relErr); %#ok<SAGROW>

    fprintf(['  subj%02d | nComp=%d | within %6.2f%% (k=%+.3f) | ' ...
             'cross %6.2f%% (k=%+.3f) | 定点 %6.2f%% (Δ%+.2f%%) | 特征相对误差 %.1e\n'], ...
        s, info.n_comp, accW*100, 2*accW-1, accC*100, 2*accC-1, ...
        accFx*100, (accFx-accC)*100, relErr);

    if s == cfg.exportSubj
        best = struct('W', W, 'w', wfold, 'b', bfold, 'info', info, ...
                      'meta', D.meta, 'sos', sos, ...
                      'accCross', accC, 'accWithin', accW);
    end
end

%% ── 汇总 ────────────────────────────────────────────────────────
aC = [R.accCross];  aW = [R.accWithin];  aF = [R.accFixed];
fprintf('\n--------------------------------------------------------------\n');
fprintf(' 平均 within 准确率 : %6.2f%%   (kappa %+.3f)\n', mean(aW)*100, 2*mean(aW)-1);
fprintf(' 平均 cross  准确率 : %6.2f%%   (kappa %+.3f)   ← 官方协议\n', mean(aC)*100, 2*mean(aC)-1);
fprintf(' 平均 定点   准确率 : %6.2f%%   相对浮点 %+.2f 个百分点\n', ...
        mean(aF)*100, (mean(aF)-mean(aC))*100);
fprintf(' 官方最优 (FBCSP)   : kappa +0.600   [bbci.de/competition/iv/results]\n');
fprintf(' Python 基线        : cross 74.37%% (kappa +0.487) / within 72.09%%\n');
fprintf('--------------------------------------------------------------\n');

save(fullfile(cfg.dataDir, 'results.mat'), 'R', 'cfg');

% ── 同时写一份 UTF-8 文本报告（Windows 控制台是 GBK，中文会乱码）──
rep = fullfile(cfg.dataDir, 'report.txt');
fr = fopen(rep, 'w', 'n', 'UTF-8');
fprintf(fr, 'CSP + SVM 验证报告  |  BCI Competition IV 2b\n');
fprintf(fr, '生成时间: %s\n', datestr(now, 'yyyy-mm-dd HH:MM:SS'));
fprintf(fr, '配置: m=%d, SVM %s C=%.1f, 正则化 %s, 滤波 %s, 窗 [%.1f %.1f]s\n\n', ...
        cfg.m, cfg.kernel, cfg.C, cfg.reg, ...
        ternary(cfg.causal,'因果','零相位'), cfg.tUse(1), cfg.tUse(2));
fprintf(fr, '%-6s %-6s %-10s %-10s %-10s %-10s %-10s\n', ...
        '被试','nComp','within','kappa','cross','kappa','定点');
fprintf(fr, '%s\n', repmat('-', 1, 66));
for i = 1:numel(R)
    fprintf(fr, 'B%02d    %-6d %6.2f%%    %+6.3f    %6.2f%%    %+6.3f    %6.2f%%\n', ...
        R(i).subj, R(i).nComp, R(i).accWithin*100, R(i).kapWithin, ...
        R(i).accCross*100, R(i).kapCross, R(i).accFixed*100);
end
fprintf(fr, '%s\n', repmat('-', 1, 66));
fprintf(fr, '平均  within %.2f%% (kappa %+.3f) | cross %.2f%% (kappa %+.3f) | 定点 %.2f%%\n', ...
        mean(aW)*100, 2*mean(aW)-1, mean(aC)*100, 2*mean(aC)-1, mean(aF)*100);
fprintf(fr, '定点 vs 浮点: %+.2f 个百分点\n\n', (mean(aF)-mean(aC))*100);
fprintf(fr, '对照: Python 基线 cross 74.37%% (kappa +0.487) / within 72.09%%\n');
fprintf(fr, '      官方最优 FBCSP kappa +0.600 [bbci.de/competition/iv/results]\n');
fclose(fr);
fprintf('\n文本报告已写入 %s\n', rep);

%% ── 导出 FPGA 参数 ──────────────────────────────────────────────
if exist('best', 'var')
    fprintf('\n导出 subj%02d 的 FPGA 参数 ...\n', cfg.exportSubj);
    fpga_export(best, cfg);
end

%% ══════════════════════════════════════════════════════════════
%  局部函数
%% ══════════════════════════════════════════════════════════════

function [Xout, dbg] = preprocess(Xraw, meta, sos, cfg)
%PREPROCESS 计数域 → 去DC → 带通 → 裁剪 → 每 trial 去均值
    [n, nch, T] = size(Xraw);
    fs = meta.fs;
    G  = meta.G;

    % 还原到 µV 便于 DC 高通（等价于在计数域做，只是尺度问题）
    X = double(Xraw) / G;

    % ── DC 去除：一阶高通 0.5 Hz，y[n] = a(y[n-1] + x[n] - x[n-1]) ──
    fc = 0.5;
    a  = 1 / (1 + 2*pi*fc/fs);
    X = filter([a -a], [1 -a], X, [], 3);      % 沿时间维（第 3 维）滤波

    % ── 带通：Cheby2 10 阶，5 节级联 ──
    Xf = zeros(size(X));
    for i = 1:n
        Xi = squeeze(X(i, :, :)).';            % (T, nch)
        Xi = biquad_cascade(Xi, sos, 'float');
        if ~cfg.causal
            Xi = flipud(biquad_cascade(flipud(Xi), sos, 'float'));   % 前向-反向 ≈ 零相位
        end
        Xf(i, :, :) = Xi.';
    end

    % ── 裁剪到使用窗 ──
    i0 = round((cfg.tUse(1) - meta.win_export(1)) * fs) + 1;
    i1 = i0 + round(diff(cfg.tUse) * fs);      % 626 点 @250Hz
    Xout = Xf(:, :, i0:i1);

    % ── 每 trial 去均值（CSP 假定零均值）──
    Xout = Xout - mean(Xout, 3);
    dbg = struct('i0', i0, 'i1', i1, 'nSamp', size(Xout, 3));
end


function [acc, kappa] = cv_within(X, y, cfg)
%CV_WITHIN 重复分层 K 折交叉验证（手写，避免工具箱版本差异）
    accs = zeros(cfg.cvFolds * cfg.cvRepeats, 1);
    p = 0;
    for rep = 1:cfg.cvRepeats
        folds = strat_folds(y, cfg.cvFolds, rep);
        for k = 1:cfg.cvFolds
            te = (folds == k);  tr = ~te;
            W  = csp_train(X(tr,:,:), y(tr), cfg.m, cfg.reg);
            Ftr = csp_features(X(tr,:,:), W);
            Fte = csp_features(X(te,:,:), W);
            [~, w, b] = fit_svm(Ftr, y(tr), cfg);
            p = p + 1;
            accs(p) = mean(predict_labels(Fte, w, b) == y(te));
        end
    end
    acc = mean(accs);
    kappa = 2*acc - 1;                          % 二分类均衡时 kappa = 2*acc-1
end


function folds = strat_folds(y, k, seed)
%STRAT_FOLDS 分层 K 折划分（保证每折类别比例一致）
    rng(1000 + seed);
    folds = zeros(numel(y), 1);
    for c = unique(y).'
        idx = find(y == c);
        idx = idx(randperm(numel(idx)));
        folds(idx) = mod(0:numel(idx)-1, k) + 1;
    end
end


function [mdl, w, b] = fit_svm(F, y, cfg)
%FIT_SVM 训练 SVM，并把线性核折叠成 w·f + b
%
%   ★ 折叠的依据（实测等价性验证见 02 号文档 §2.6）：
%        d(f) = Σ αᵢ yᵢ (svᵢ·f) + b = (Σ αᵢ yᵢ svᵢ)·f + b
%     折叠后 PL 只需存 n_comp 个权重 + 1 个偏置，
%     而不是 K 个支持向量 —— BRAM 占用降两个数量级。
%
%   注意 MATLAB 与 libsvm 的字段名不同：
%     MATLAB : mdl.Alpha (α) × mdl.SupportVectorLabels (±1)
%     libsvm : sv_coef 已内含符号
    mdl = fitcsvm(F, y, 'KernelFunction', cfg.kernel, ...
                  'BoxConstraint', cfg.C, 'Standardize', false);
    if strcmpi(cfg.kernel, 'linear')
        % ── 折叠 ──
        coef = mdl.Alpha .* mdl.SupportVectorLabels;     % αᵢ·yᵢ
        w = (mdl.SupportVectors.' * coef);
        b = mdl.Bias;

        % ── 自洽验证：折叠后的 w·f+b 必须等于直接按核展开算出的决策值 ──
        K      = F * mdl.SupportVectors.';               % 线性核
        d_direct = K * coef + mdl.Bias;
        d_fold   = F * w + b;
        scale = max(1, max(abs(d_direct)));
        assert(max(abs(d_fold - d_direct)) < 1e-9 * scale, ...
               'SVM 折叠失败：w·f+b 与核展开不一致');

        % ── 符号自校正：MATLAB 的类别符号约定可能与 0/1 编码相反 ──
        if mean(double(F*w + b > 0) == y) < 0.5
            w = -w;  b = -b;
        end
        assert(mean(double(F*w + b > 0) == y) > 0.5, 'SVM 符号校正失败');
    else
        w = []; b = [];
    end
end


function yhat = predict_labels(F, w, b)
%PREDICT_LABELS 用折叠后的权重做预测（线性核）
    yhat = double(F * w + b > 0);
end


function s = ternary(c, a, b)
    if c, s = a; else, s = b; end
end
