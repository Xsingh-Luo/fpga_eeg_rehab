%% VALIDATE_PIPELINE 验证"导出参数 + 本目录 MATLAB 函数"的推理链路是可信的
%
%   本脚本用 BCI Competition IV 2b 的 B04 评价数据复算 train_final.m 的结果，
%   为 predict_record.m 在新记录上的推理提供依据：
%
%   ① 用 params/golden_vectors.mat 里导出的 W_eff（baked 口径）在 B04 评价集上算准确率
%      - 评价 session 2（05E）：W_eff 正是用该 session 前 20 个试次做 EA 折叠的，
%        应精确复现 train_log.txt 记录的 93.57%；
%      - 评价 session 1（04E）：用同一套 W_eff（EA 是另一个 session 的），仅作参考。
%   ② 检查首试次的 CSP 投影 z = W_eff' * x 与 golden_vectors.mat 的 z_expected 一致；
%   ③ 检查"反解未折叠 W → 再用 05E 校准矩阵重新折叠"能还原 W_eff（<=1e-12）。
%
%   用法: matlab -batch "cd('H:\FPGA2026QS\matlab'); validate_pipeline"
%   全部通过时最后打印 "PASS"，否则打印 "FAIL"。

clear; clc;
matlabDir = fileparts(mfilename('fullpath'));
root = fileparts(matlabDir);
addpath(matlabDir);

fprintf('==============================================================\n');
fprintf(' validate_pipeline | BCI IV 2b B04 复算（验证推理链路）\n');
fprintf('==============================================================\n');

cfg = struct('tUse', [3.5 6.0], 'causal', true);

GM = load(fullfile(root, 'params', 'golden_vectors.mat'));
g  = GM.GOLD{4};
F  = load(fullfile(matlabDir, 'data', 'fbcsp_filters.mat'));
SOS3 = arrayfun(@(i) squeeze(F.sos3(i,:,:)), 1:size(F.sos3,1), 'uni', 0);
D = load(fullfile(matlabDir, 'data', 'subj04.mat'));

fprintf('数据: subj04.mat Xte = %d x %d x %d, yte = %d\n', ...
        size(D.Xte), numel(D.yte));
fprintf('参数: golden GOLD{4}, W_eff %d 带, selIdx = %s\n', numel(g.W_eff), mat2str(g.selIdx.'));

Bte = prep_bands(D.Xte, D.meta, SOS3, cfg);      % (320,3,626,3)
per = floor(numel(D.yte) / 2);
nCal = 20;

acc = nan(1,2);
for ss = 1:2
    rSess = (ss-1)*per + (1:per);
    Bs = Bte(rSess,:,:,:);
    ys = double(D.yte(rSess));
    acc(ss) = eval_acc(Bs, g, ys, (nCal+1):per);
    fprintf('  session %d (S%d): 准确率 %.2f%%  (评估第 %d-%d 试次)\n', ...
            ss, ss, acc(ss)*100, nCal+1, per);
end

% ② 首试次 z 一致性（session 2 的第 1 个试次）
zErr = 0;
for bb = 1:numel(SOS3)
    Xi = squeeze(Bte(per+1,:,:,bb));
    Z  = g.W_eff{bb}.' * Xi;
    zErr = max(zErr, max(abs(Z(:) - g.z_expected{bb}(:))));
end
fprintf('  CSP 投影 z 与 golden z_expected 最大差: %.3e\n', zErr);

% ③ 反解 W 再折叠
WunErr = 0;
for bb = 1:numel(SOS3)
    [~, M2] = euclid_align(Bte(per+1:end,:,:,bb), 20);
    Wun = M2.' \ g.W_eff{bb};
    Wback = M2.' * Wun;
    WunErr = max(WunErr, max(abs(Wback(:) - g.W_eff{bb}(:))));
end
fprintf('  反解 W 再折叠回 W_eff 的最大误差: %.3e\n', WunErr);

% 判定
accOK  = abs(acc(2)*100 - 93.57) < 0.1;
zOK    = zErr < 1e-9;
wOK    = WunErr < 1e-10;
fprintf('\n---------------- 判定 ----------------\n');
fprintf('  复现 05E 准确率 93.57%% : %s (实测 %.2f%%)\n', pass_str(accOK), acc(2)*100);
fprintf('  z 与 golden 一致        : %s (%.2e)\n', pass_str(zOK), zErr);
fprintf('  W 反解/再折叠一致       : %s (%.2e)\n', pass_str(wOK), WunErr);
if accOK && zOK && wOK
    fprintf('\nPASS —— predict_record.m 使用的推理链路与 train_final.m 一致。\n');
else
    fprintf('\nFAIL —— 推理链路与 train_final.m 不一致，请检查。\n');
end
fprintf('==============================================================\n');

% ---------- 局部函数 ----------
function acc = eval_acc(B, g, y, rTest)
    nb = numel(g.W_eff); Fall = [];
    for bb = 1:nb
        Fall = [Fall, csp_features(B(rTest,:,:,bb), g.W_eff{bb})];  %#ok<AGROW>
    end
    Fs = Fall(:, g.selIdx);
    d  = ((Fs - g.mu)./g.sd) * g.w + g.b;
    acc = mean((d(:) > 0) == (y(rTest) == 1));
end

function s = pass_str(ok)
    if ok, s = 'OK  '; else, s = 'FAIL'; end
end
