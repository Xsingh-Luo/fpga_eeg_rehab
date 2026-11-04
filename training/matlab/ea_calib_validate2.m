%% S1b · 真实校准场景（按 session 内校准，修正 S1 的设计缺陷）
%
%  S1 的缺陷：把 04E+05E 拼成 320 个试次后"取前 N 个做校准"。
%  当 N=160 时，校准集恰好是 04E、测试集恰好是 05E —— **跨 session 了**，
%  而 EA 的设计前提是用**目标域自己**的数据估白化矩阵。
%
%  真实部署场景是：被试到位 → 做 N 个试次校准 → 接着做正式任务，
%  两者**同一个 session**。本脚本按这个口径重做。
%
%  对每个评价 session（04E / 05E）分别：
%      校准 = 该 session 的前 N 个试次
%      测试 = 该 session 的剩余 160−N 个试次
%  并对比有/无 EA 在**同一批测试试次**上的表现。
%
%  用法: matlab -batch "cd('H:\FPGA2026QS\matlab'); ea_calib_validate2"

clear; clc;
fprintf('====================================================================\n');
fprintf(' S1b · 真实校准场景（session 内校准）\n');
fprintf(' 3 子带 FBCSP + 欧氏对齐 + 线性 SVM  |  BCI IV 2b  |  cross 协议\n');
fprintf('====================================================================\n');

cfg = struct();
cfg.dataDir  = fullfile(fileparts(mfilename('fullpath')), 'data');
cfg.subjects = 1:9;
cfg.m        = 1;
cfg.kSelect  = 4;
cfg.C        = 1.0;
cfg.causal   = true;
cfg.tUse     = [3.5 6.0];
NCAL         = [10 20 40 80];

F = load(fullfile(cfg.dataDir, 'fbcsp_filters.mat'));
SOS3 = arrayfun(@(i) squeeze(F.sos3(i,:,:)), 1:size(F.sos3,1), 'uni', 0);

fprintf('子带 %d 个 | m=%d | k=%d | 因果=%d | 校准量 %s\n\n', ...
        numel(SOS3), cfg.m, cfg.kSelect, cfg.causal, mat2str(NCAL));

subs = cfg.subjects(:)';
nS = numel(subs);
nN = numel(NCAL);
nSess = 2;
EA = nan(nS, nSess, nN);      % 有 EA
NO = nan(nS, nSess, nN);      % 无 EA

for si = 1:nS
    s = subs(si);
    f = fullfile(cfg.dataDir, sprintf('subj%02d.mat', s));
    if ~isfile(f), continue; end
    D = load(f);
    ytr = double(D.ytr(:));  yte = double(D.yte(:));

    Btr0 = prep_bands(D.Xtr, D.meta, SOS3, cfg);
    Bte0 = prep_bands(D.Xte, D.meta, SOS3, cfg);

    % 训练域对齐（用全部训练数据）
    BtrA = align_train(Btr0);

    nTe = size(Bte0, 1);
    per = nTe / nSess;                      % 每个 session 的试次数（应为 160）
    fprintf('B%02d  (每 session %d 试次)\n', s, per);

    for ss = 1:nSess
        rSess = (ss-1)*per + (1:per);
        Bs = Bte0(rSess, :, :, :);
        ys = yte(rSess);
        fprintf('   %s  ', char('A' + ss - 1));
        for i = 1:nN
            n = NCAL(i);
            rTest = (n+1):per;

            % ── 有 EA：训练域与测试域都用各自的 R̄ 对齐（域一致）──
            BsA = align_test(Bs, n);
            EA(si, ss, i) = fbcsp_eval(BtrA, BsA(rTest,:,:,:), ytr, ys(rTest), cfg);

            % ── 无 EA：训练域与测试域都**不**对齐（域一致）──
            %   ★ 不能拿 BtrA 配未对齐的 Bs —— 那会造成域不匹配，把基线压低好几个点
            NO(si, ss, i) = fbcsp_eval(Btr0, Bs(rTest,:,:,:), ytr, ys(rTest), cfg);

            fprintf('N=%d: EA %5.2f%% 无EA %5.2f%% (%+.2f)   ', ...
                    n, EA(si,ss,i)*100, NO(si,ss,i)*100, (EA(si,ss,i)-NO(si,ss,i))*100);
        end
        fprintf('\n');
    end
end

%% ── 汇总 ──
fprintf('\n%s\n', repmat('=', 1, 92));
fprintf('%-14s', '汇总');
for i = 1:nN, fprintf('%22s', sprintf('N=%d', NCAL(i))); end
fprintf('\n%-14s', '');
for i = 1:nN, fprintf('%8s%14s', '有EA', '无EA'); end
fprintf('\n%s\n', repmat('-', 1, 14 + 22*nN));

mEA = squeeze(mean(mean(EA, 2, 'omitnan'), 1, 'omitnan'));
mNO = squeeze(mean(mean(NO, 2, 'omitnan'), 1, 'omitnan'));
fprintf('%-14s', '两个 session 平均');
for i = 1:nN, fprintf('%7.2f%%%13.2f%%', mEA(i)*100, mNO(i)*100); end
fprintf('\n%-14s', 'kappa(EA)');
for i = 1:nN, fprintf('%21.3f', 2*mEA(i)-1); end
fprintf('\n%-14s', 'EA 净增益(pp)');
for i = 1:nN, fprintf('%21.2f', (mEA(i)-mNO(i))*100); end
fprintf('\n%s\n', repmat('-', 1, 14 + 22*nN));

% 分 session 看
for ss = 1:nSess
    a = squeeze(mean(EA(:,ss,:), 1, 'omitnan'));
    b = squeeze(mean(NO(:,ss,:), 1, 'omitnan'));
    fprintf('%-14s', sprintf('session %d 增益', ss));
    for i = 1:nN, fprintf('%21.2f', (a(i)-b(i))*100); end
    fprintf('\n');
end
fprintf('%s\n', repmat('-', 1, 14 + 22*nN));
fprintf('对照: 2008 官方冠军 kappa +0.600\n');

save(fullfile(cfg.dataDir, 'calib2_results.mat'), 'EA', 'NO', 'NCAL', 'cfg');
fprintf('\n结果已存 calib2_results.mat\n');

%% ══════════════════════════════════════════════════════════════════
function A = align_train(Btr)
%ALIGN_TRAIN 训练域逐带对齐（用全部训练试次估 R̄）
    nb = size(Btr, 4);
    A = zeros(size(Btr));
    for b = 1:nb
        [A(:,:,:,b), ~] = euclid_align(Btr(:, :, :, b));
    end
end

function B = align_test(Bte, nCal)
%ALIGN_TEST 测试域逐带对齐（只用前 nCal 个试次估 R̄）
    nb = size(Bte, 4);
    B = zeros(size(Bte));
    for b = 1:nb
        [B(:,:,:,b), ~] = euclid_align(Bte(:, :, :, b), nCal);
    end
end
