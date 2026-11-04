%% S1 · 真实校准场景下的 EA 验证
%
%  上一轮实验的口径问题是：**用评价段前 N 个试次估白化矩阵，却在全部 320 个试次上算准确率**
%  —— 校准集与测试集重叠，结果偏乐观。
%
%  本脚本对比三种口径：
%    A 乐观口径：校准前 N 试次，测试**全部** 320 试次        （= 上一轮的做法）
%    B 真实口径：校准前 N 试次，测试**剩余** 320−N 试次      （校准与测试严格分离）
%    C 无 EA   ：不做对齐，测试剩余 320−N 试次               （B 的对照基线）
%
%  B 才是真实部署能拿到的数字：现场先做 N 个试次校准，再开始正式任务，
%  校准的那几个试次本来就不计入准确率。
%
%  用法: matlab -batch "cd('H:\FPGA2026QS\matlab'); ea_calib_validate"

clear; clc;
fprintf('====================================================================\n');
fprintf(' S1 · 真实校准场景下的 EA 验证\n');
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
NCAL         = [10 20 40 80 160];

F = load(fullfile(cfg.dataDir, 'fbcsp_filters.mat'));
SOS3 = arrayfun(@(i) squeeze(F.sos3(i,:,:)), 1:size(F.sos3,1), 'uni', 0);

fprintf('子带 %d 个 | m=%d | k=%d | 因果=%d | 校准量 %s\n\n', ...
        numel(SOS3), cfg.m, cfg.kSelect, cfg.causal, mat2str(NCAL));

subs = cfg.subjects(:)';
nS = numel(subs);
nN = numel(NCAL);
A = nan(nS, nN);      % 乐观口径
B = nan(nS, nN);      % 真实口径（有 EA）
C = nan(nS, nN);      % 真实口径（无 EA）

fprintf('%-6s', '被试');
for i = 1:nN, fprintf('%22s', sprintf('N=%d', NCAL(i))); end
fprintf('\n%-6s', '');
for i = 1:nN, fprintf('%7s%7s%8s', 'A乐观', 'B真实', 'C无EA'); end
fprintf('\n%s\n', repmat('-', 1, 6 + 22*nN));

for si = 1:nS
    s = subs(si);
    f = fullfile(cfg.dataDir, sprintf('subj%02d.mat', s));
    if ~isfile(f), continue; end
    D = load(f);
    ytr = double(D.ytr(:));  yte = double(D.yte(:));
    nTe = numel(yte);

    Btr0 = prep_bands(D.Xtr, D.meta, SOS3, cfg);
    Bte0 = prep_bands(D.Xte, D.meta, SOS3, cfg);

    fprintf('B%02d   ', s);
    for i = 1:nN
        n = NCAL(i);
        if n >= nTe - 10
            fprintf('%22s', '—');  continue;
        end
        % ── 对齐：训练域用全部数据，测试域只用前 n 个试次估 M ──
        [BtrA, BteA] = align_bands(Btr0, Bte0, n);

        % A：乐观口径（测试全部 320 试次，含用于校准的那 n 个）
        A(si, i) = fbcsp_eval(BtrA, BteA, ytr, yte, cfg);

        % B：真实口径（只测剩余试次，校准试次不计入）
        rTest = (n+1):nTe;
        B(si, i) = fbcsp_eval(BtrA, BteA(rTest,:,:,:), ytr, yte(rTest), cfg);

        % C：无 EA，测同一批剩余试次
        C(si, i) = fbcsp_eval(Btr0, Bte0(rTest,:,:,:), ytr, yte(rTest), cfg);

        fprintf('%6.1f%%%6.1f%%%7.1f%%', A(si,i)*100, B(si,i)*100, C(si,i)*100);
    end
    fprintf('\n');
end

%% ── 汇总 ──
fprintf('\n%s\n', repmat('=', 1, 6 + 22*nN));
mA = mean(A, 1, 'omitnan');
mB = mean(B, 1, 'omitnan');
mC = mean(C, 1, 'omitnan');
fprintf('%-6s', 'A 乐观');
for i = 1:nN, fprintf('%21.2f%%', mA(i)*100); end
fprintf('\n%-6s', 'B 真实');
for i = 1:nN, fprintf('%21.2f%%', mB(i)*100); end
fprintf('\n%-6s', 'C 无EA');
for i = 1:nN, fprintf('%21.2f%%', mC(i)*100); end
fprintf('\n%-6s', '');
for i = 1:nN, fprintf('%21.2f ', (mB(i)-mC(i))*100); end
fprintf('  ← EA 净增益(pp)\n');
fprintf('%-6s', '');
for i = 1:nN, fprintf('%21.2f ', (mA(i)-mB(i))*100); end
fprintf('  ← 乐观口径高估(pp)\n');
fprintf('%s\n', repmat('-', 1, 6 + 22*nN));
fprintf('对照: 2008 官方冠军 kappa +0.600\n');

save(fullfile(cfg.dataDir, 'calib_results.mat'), 'A', 'B', 'C', 'NCAL', 'cfg');
fprintf('\n结果已存 calib_results.mat\n');

%% ══════════════════════════════════════════════════════════════════
function [A, B] = align_bands(Btr, Bte, nCal)
%ALIGN_BANDS 逐带欧氏对齐：训练域用全部数据，测试域只用前 nCal 个试次估 R̄
    nb = size(Btr, 4);
    A = zeros(size(Btr));  B = zeros(size(Bte));
    for b = 1:nb
        [A(:,:,:,b), ~] = euclid_align(Btr(:, :, :, b));
        [B(:,:,:,b), ~] = euclid_align(Bte(:, :, :, b), nCal);
    end
end

