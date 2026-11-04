%% S2 · 最终配置训练 + EA 折叠 + 参数导出 + 定点校验
%
%  完整走一遍"从数据到可下发参数"的流程：
%    ① 训练集（01T+02T+03T）逐带欧氏对齐 → CSP 训练 → MIBIF 选择 → SVM 训练
%    ② 用测试集的**前 20 个试次**做校准，算 M_b，折叠 W_eff = M_bᵀ·W̃_b
%    ③ 量化导出（C 头文件 + JSON）
%    ④ 定点 vs 浮点校验（逐位模拟 PL 的整数运算）
%    ⑤ 生成 golden vector（供 PL 逐级对拍）
%
%  用法: matlab -batch "cd('H:\FPGA2026QS\matlab'); train_final"

clear; clc;
fprintf('====================================================================\n');
fprintf(' S2 · 最终配置训练 → EA 折叠 → 参数导出 → 定点校验\n');
fprintf('====================================================================\n');

cfg = struct();
cfg.dataDir  = fullfile(fileparts(mfilename('fullpath')), 'data');
cfg.outDir   = fullfile(fileparts(mfilename('fullpath')), '..', 'params');
cfg.subjects = 1:9;
cfg.m        = 1;
cfg.kSelect  = 4;
cfg.C        = 1.0;
cfg.causal   = true;
cfg.tUse     = [3.5 6.0];
cfg.nCal     = 20;
rng(42);

F = load(fullfile(cfg.dataDir, 'fbcsp_filters.mat'));
SOS3 = arrayfun(@(i) squeeze(F.sos3(i,:,:)), 1:size(F.sos3,1), 'uni', 0);
BANDS = F.bands3;

fprintf('配置: %d 子带 | m=%d | MIBIF k=%d | 校准 %d 试次 | 因果=%d\n\n', ...
        numel(SOS3), cfg.m, cfg.kSelect, cfg.nCal, cfg.causal);

subs = cfg.subjects(:)';
nS = numel(subs);
ACC = nan(nS, 1);  ACC_FIX = nan(nS, 1);
GOLD = cell(nS, 1);

for si = 1:nS
    s = subs(si);
    f = fullfile(cfg.dataDir, sprintf('subj%02d.mat', s));
    if ~isfile(f), continue; end
    D = load(f);  meta = D.meta;
    ytr = double(D.ytr(:));  yte = double(D.yte(:));
    nTe = numel(yte);

    % ① 训练域：逐带对齐 → CSP → 特征
    Btr0 = prep_bands(D.Xtr, meta, SOS3, cfg);
    Bte0 = prep_bands(D.Xte, meta, SOS3, cfg);

    nBand = numel(SOS3);
    BtrA = zeros(size(Btr0));  Mtr = cell(1, nBand);
    for b = 1:nBand
        [BtrA(:,:,:,b), Mtr{b}] = euclid_align(Btr0(:,:,:,b));
    end

    W = cell(1, nBand);  Ftr = [];
    for b = 1:nBand
        W{b} = csp_train(BtrA(:,:,:,b), ytr, cfg.m, 'lw');
        Ftr = [Ftr, csp_features(BtrA(:,:,:,b), W{b})];  %#ok<AGROW>
    end
    selIdx = mibif_select(Ftr, ytr, cfg.kSelect);
    FtrS = Ftr(:, selIdx);
    mu = mean(FtrS, 1);  sd = std(FtrS, 0, 1);  sd(sd < eps) = 1;
    mdl = fitcsvm((FtrS-mu)./sd, ytr, 'KernelFunction','linear', ...
                  'BoxConstraint', cfg.C, 'Standardize', false);
    % SVM 折叠到未标准化域：d = w·((f-mu)/sd) + b  =>  w' = w./sd, b' = b - sum(w.*mu./sd)
    wRaw = mdl.Beta(:) ./ sd(:);
    bRaw = mdl.Bias - sum(mdl.Beta(:) .* mu(:) ./ sd(:));

    % ② 部署：**按 session 分别校准**
    %   真实场景是"被试到位 → 做 nCal 个试次校准 → 接着做正式任务"，两者同一 session。
    %   ★ 若把 04E+05E 拼起来取前 nCal 个做校准，测到另一个 session 上就是跨域误用，
    %     准确率会被压低好几个点（这是 S1 踩过的坑）。
    nCal = cfg.nCal;
    per  = floor(nTe / 2);
    accF = nan(1,2);  accX = nan(1,2);
    q = struct('zf', 13, 'ff', 11);
    WeffLast = [];  BteALast = [];  yteLast = [];
    for ss = 1:2
        rSess = (ss-1)*per + (1:per);
        Bs = Bte0(rSess, :, :, :);
        ys = yte(rSess);
        BsA = zeros(size(Bs));  Weff = cell(1, nBand);
        for b = 1:nBand
            [BsA(:,:,:,b), Mb] = euclid_align(Bs(:,:,:,b), nCal);
            Weff{b} = Mb.' * W{b};                  % ★ EA 折叠
        end
        rTest = (nCal+1):per;
        % ★ 折叠后必须用**未对齐**的 Bs：
        %     Weff^T · x = (M^T W)^T x = W^T M x = W^T · x_aligned
        %   若这里传 BsA（已对齐），等于把 M 施加两次。
        accF(ss) = eval_feat(Bs, Weff, selIdx, mu, sd, mdl, ys, rTest);
        accX(ss) = eval_feat_fixed(Bs, Weff, selIdx, mu, sd, wRaw, bRaw, ys, rTest, q);
        WeffLast = Weff;  BteALast = Bs;  yteLast = ys;
    end
    ACC(si) = mean(accF);  ACC_FIX(si) = mean(accX);
    BteA = BteALast;  Weff = WeffLast;   % 供 golden vector 使用

    % ⑤ golden vector：取前 5 个测试试次，记录每一级的中间结果
    g = struct();
    g.subj = s;  g.nCal = nCal;
    g.selIdx = selIdx;  g.bands = BANDS;
    g.input_int16 = int16(round(double(D.Xte(1:5,:,:))));   % 完整 epoch，供 PL 从原始输入复算
    g.meta_fs = D.meta.fs;  g.meta_G = D.meta.G;  g.meta_win = D.meta.win_export;
    g.W_eff = Weff;  g.mu = mu;  g.sd = sd;  g.w = wRaw;  g.b = bRaw;
    g.z_expected = cell(1, nBand);
    for b = 1:nBand
        Xi = squeeze(BteA(1,:,:,b));        % (n_ch, T) —— 未对齐原始数据，不要再转置
        g.z_expected{b} = Weff{b}.' * Xi;   % (n_comp, T)
    end
    % ★ 特征按**带**纵向拼接成 (n_feat_all × 1)，与训练时的拼接顺序一致。
    %   不能用 cell2mat：它会把 3 个 (n_comp×1) **横着**拼成 (n_comp×3)。
    fv = cellfun(@(Z) log(max(var(Z,0,2),1e-30) ./ sum(max(var(Z,0,2),1e-30),2)), ...
                 g.z_expected, 'uni', 0);
    g.feat_expected = vertcat(fv{:});                    % (6×1)
    g.feat_sel = g.feat_expected(selIdx);                % (4×1)
    % ★ 显式写成行向量点积，避免 (4×1)-(1×4) 触发广播得到 4×4
    g.decision_expected = ((g.feat_sel.' - mu) ./ sd) * wRaw + bRaw;   % 标量
    GOLD{si} = g;

    aF = mean(accF);  aX = mean(accX);
    fprintf('B%02d  浮点 %6.2f%% (κ%+.3f)  [S1 %5.2f%% S2 %5.2f%%]   定点 %6.2f%% (Δ%+.2f pp)   |W_eff|max=%.2f\n', ...
            s, aF*100, 2*aF-1, accF(1)*100, accF(2)*100, aX*100, (aX-aF)*100, ...
            max(cellfun(@(M) max(abs(M(:))), Weff)));
end

fprintf('\n%s\n', repmat('-', 1, 76));
fprintf('9 被试平均:  浮点 %.2f%% (κ%+.3f)   定点 %.2f%% (κ%+.3f)   差 %+.2f pp\n', ...
        mean(ACC,'omitnan')*100, 2*mean(ACC,'omitnan')-1, ...
        mean(ACC_FIX,'omitnan')*100, 2*mean(ACC_FIX,'omitnan')-1, ...
        (mean(ACC_FIX,'omitnan')-mean(ACC,'omitnan'))*100);

%% ③ 导出：用 B04（响应最好）作为演示被试
demoIdx = find(subs == 4, 1);
if isempty(demoIdx) || isnan(ACC(demoIdx)), demoIdx = 1; end
s = subs(demoIdx);
D = load(fullfile(cfg.dataDir, sprintf('subj%02d.mat', s)));
fprintf('\n导出演示被试 B%02d（cross %.2f%%）：\n', s, ACC(demoIdx)*100);

best = struct();
best.meta = D.meta;  best.meta.subject = s;
best.sos = SOS3;  best.bands = BANDS;
best.m = cfg.m;  best.nCal = cfg.nCal;  best.accCross = ACC(demoIdx);
g = GOLD{demoIdx};
best.W = g.W_eff;  best.selIdx = g.selIdx;
best.mu = g.mu;  best.sd = g.sd;  best.w = g.w;  best.b = g.b;
fpga_export(best, cfg);

%% ⑤ 写 golden vector
gp = fullfile(cfg.outDir, 'golden_vectors.mat');
save(gp, 'GOLD', 'cfg', '-v7');   % v7 而非 v7.3：scipy.io.loadmat 可直接读
fprintf('\n  -> %s（%d 个被试的逐级期望输出）\n', gp, nS);

save(fullfile(cfg.dataDir, 'final_train.mat'), 'ACC', 'ACC_FIX', 'cfg');
fprintf('\n完成。\n');

%% ══════════════════════════════════════════════════════════════════
function acc = eval_feat(BteA, Weff, selIdx, mu, sd, mdl, yte, rTest)
    nb = numel(Weff);  F = [];
    for b = 1:nb
        F = [F, csp_features(BteA(rTest,:,:,b), Weff{b})];  %#ok<AGROW>
    end
    F = F(:, selIdx);
    acc = mean(double(predict(mdl, (F-mu)./sd)) == yte(rTest));
end

function acc = eval_feat_fixed(BteA, Weff, selIdx, mu, sd, wRaw, bRaw, yte, rTest, q)
%EVAL_FEAT_FIXED 逐位模拟 PL 的整数运算：z=Wᵀx(Q13) → 方差 → 归一化对数 → 点积
    nb = numel(Weff);
    Fq = [];
    for b = 1:nb
        X = BteA(rTest,:,:,b);
        n = size(X,1);
        % CSP 投影，Q13
        zq = zeros(n, size(Weff{b},2), size(X,3));
        for i = 1:n
            Zi = Weff{b}.' * squeeze(X(i,:,:));
            zq(i,:,:) = round(Zi * 2^q.zf);
        end
        zq = zq / 2^q.zf;
        % ★ 方差必须沿**时间轴**（dim 3）；zq 的形状是 (n_trials, n_comp, T)
        v = max(var(zq, 0, 3), 1e-30);          % (n, n_comp, 1)
        v = squeeze(v);                          % (n, n_comp)
        if size(v,2) ~= size(Weff{b},2), v = v.'; end
        fq = log(v ./ sum(v, 2));
        Fq = [Fq, fq];  %#ok<AGROW>
    end
    Fq = Fq(:, selIdx);
    % 特征量化 Q11 后判决
    %   ★ wRaw = Beta./sd、bRaw = Bias - sum(Beta.*mu./sd) 已经把标准化折进去了，
    %     所以这里 **不能** 再算 (f-mu)./sd —— 那会把 1/sd 施加两次。
    fqi = round(Fq * 2^q.ff) / 2^q.ff;
    d = fqi * wRaw + bRaw;
    acc = mean((d > 0) == (yte(rTest) == 1));
end
