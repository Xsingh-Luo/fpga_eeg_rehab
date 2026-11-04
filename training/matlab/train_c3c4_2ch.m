%% TRAIN_C3C4_2CH 训练"2 通道 C3/C4"模型（给无法改电极的默认 OpenBCI 头套用）
%
%   场景：头套电极位置固定为 OpenBCI 默认 8 通道
%   （CH1=Fp1, CH2=Fp2, CH3=C3, CH4=C4, CH5=P7, CH6=P8, CH7=O1, CH8=O2），
%   无法改电极，但 C3/C4 恰好是 CH3/CH4 —— 还是能拿到左右手最关键的两路。
%   本脚本用 BCI IV 2b 的 B04 数据训练一个只有 C3/C4 两通道的 3 带 FBCSP+EA+SVM，
%   并在 04E/05E 上评估，最后套到 record/1.mat（取它的 CH3/CH4）验证。
%
%   用法: matlab -batch "cd('H:\FPGA2026QS\matlab'); train_c3c4_2ch"
%   输出: ../sessions/predict_record/model_c3c4_2ch.mat

matlabDir = fileparts(mfilename('fullpath'));
root      = fileparts(matlabDir);
addpath(matlabDir);

cfg = struct();
cfg.dataDir = fullfile(matlabDir, 'data');
cfg.m = 1; cfg.kSelect = 4; cfg.C = 1.0; cfg.causal = true;
cfg.tUse = [3.5 6.0]; cfg.nCal = 20;
rng(42);

F = load(fullfile(cfg.dataDir, 'fbcsp_filters.mat'));
SOS3 = arrayfun(@(i) squeeze(F.sos3(i,:,:)), 1:size(F.sos3,1), 'uni', 0);
bands = F.bands3;

D = load(fullfile(cfg.dataDir, 'subj04.mat'));
% meta.ch_names = {'C3','Cz','C4'}（见 python/export_2b_to_mat.py），C3/C4 = 第 1/3 路
chSel = [1 3];
fprintf('训练数据: B04, 通道 = C3/C4 (Xtr 索引 %s)\n', mat2str(chSel));
Xtr = D.Xtr(:, chSel, :);  Xte = D.Xte(:, chSel, :);
ytr = double(D.ytr(:));    yte = double(D.yte(:));

% ---------- 训练 ----------
Btr0 = prep_bands(Xtr, D.meta, SOS3, cfg);
nB = numel(SOS3);
BtrA = zeros(size(Btr0)); W = cell(1, nB);
for b = 1:nB
    [BtrA(:,:,:,b), ~] = euclid_align(Btr0(:,:,:,b));
    W{b} = csp_train(BtrA(:,:,:,b), ytr, cfg.m, 'lw');
end
Ftr = [];
for b = 1:nB, Ftr = [Ftr, csp_features(BtrA(:,:,:,b), W{b})]; end
selIdx = mibif_select(Ftr, ytr, cfg.kSelect);
FtrS = Ftr(:, selIdx);
mu = mean(FtrS, 1);  sd = std(FtrS, 0, 1);  sd(sd < eps) = 1;
mdl = fitcsvm((FtrS-mu)./sd, ytr, 'KernelFunction','linear', ...
              'BoxConstraint',cfg.C, 'Standardize', false);
wRaw = mdl.Beta(:) ./ sd(:);
bRaw = mdl.Bias - sum(mdl.Beta(:) .* mu(:) ./ sd(:));
fprintf('CSP: 每带 %d 分量 x %d 带 = %d 维特征，MIBIF 选 %d 维: %s\n', ...
        size(W{1},2), nB, nB*size(W{1},2), numel(selIdx), mat2str(selIdx.'));

% ---------- 2b 评估（每个 session 前 20 trial 做 EA 校准）----------
Bte0 = prep_bands(Xte, D.meta, SOS3, cfg);
per = floor(numel(yte)/2);
acc = nan(1,2);
for ss = 1:2
    rSess = (ss-1)*per + (1:per);
    Bs = Bte0(rSess,:,:,:);  ys = yte(rSess);
    Weff = cell(1,nB);
    for b = 1:nB
        [~, Mb] = euclid_align(Bs(:,:,:,b), cfg.nCal);
        Weff{b} = Mb.' * W{b};
    end
    d = decode(Bs, Weff, selIdx, mu, sd, wRaw, bRaw);
    rTest = (cfg.nCal+1):per;
    acc(ss) = mean((d(rTest)>0) == (ys(rTest)==1));
    fprintf('  2b 评价 session %d: %.2f%%\n', ss, 100*acc(ss));
end
fprintf('  2b 平均: %.2f%%  (作为对照：3 通道 B04 为 95.71%%)\n', 100*mean(acc));

% ---------- 保存模型 ----------
model = struct();
model.ch_names = {'C3','C4'};  model.ch_sel_in_2b = chSel;
model.subject = 4;  model.bands = bands;  model.m = cfg.m;
model.W = {W{:}};  model.selIdx = selIdx;
model.mu = mu;  model.sd = sd;  model.w = wRaw;  model.b = bRaw;
model.accCross = mean(acc);  model.accPerSession = acc;
model.notes = '2-channel C3/C4 FBCSP+EA+SVM, trained on BCI IV 2b B04 01T-03T';
modelPath = fullfile(root, 'sessions', 'predict_record', 'model_c3c4_2ch.mat');
save(modelPath, 'model', 'cfg', '-v7');
fprintf('模型已保存: %s\n', modelPath);

% ---------- 套到 record/1.mat（默认头套下 C3=CH3, C4=CH4）----------
recPath = fullfile(root, 'record', '1.mat');
S = load(recPath);
X8 = double(S.RecordData.NowData);
fs = 250;
X = X8([3 4], :).';          % CH3=C3, CH4=C4
X = X * 294.89;              % µV -> counts（与训练同域；对 EA/特征无影响）
T = size(X,1);
a = 1/(1+2*pi*0.5/fs);
Xd = filter([a -a], [1 -a], X, [], 1);
winLen = round(2.5*fs)+1; stride = round(0.5*fs);
Bf = zeros(T, 2, nB);
for b = 1:nB, Bf(:,:,b) = biquad_cascade(Xd, SOS3{b}, 'float'); end
nWin = floor((T-winLen)/stride)+1;
starts = 1 + (0:nWin-1)*stride;  tSec = (starts-1)/fs;
Xw = zeros(nWin, 2, winLen, nB);
for b = 1:nB
    for k = 1:nWin
        seg = Bf(starts(k):starts(k)+winLen-1, :, b);
        Xw(k,:,:,b) = (seg - mean(seg,1)).';
    end
end
% 同 session EA 校准（前 20 个窗，跳过开头 3 s）
cand = find(tSec >= 3.0, 1, 'first');
calW = cand:min(nWin, cand+cfg.nCal-1);
Weff = cell(1,nB);
for b = 1:nB
    [~, Mb] = euclid_align(Xw(calW,:,:,b), numel(calW));
    Weff{b} = Mb.' * W{b};
end
d = decode(Xw, Weff, selIdx, mu, sd, wRaw, bRaw);

% 已知真实标签：前段=左手(0)，后段=右手(1)（按 18.75 s 对半）
mid = tSec(:) + 2.5/2;
y = double(mid >= 18.75);
accRec = mean((d>0) == (y==1));
accFront = mean((d(mid<18.75)) < 0);   % 前段判左手
accBack  = mean((d(mid>=18.75)) > 0);  % 后段判右手
% AUC
pos = d(y==1); neg = d(y==0); auc = mean(pos(:) > neg(:).', 'all');
fprintf('\nRECORD record/1.mat (CH3=C3, CH4=C4):\n');
fprintf('  window acc = %.1f%% | front(LEFT) acc = %.1f%% | back(RIGHT) acc = %.1f%% | AUC = %.3f\n', ...
        100*accRec, 100*accFront, 100*accBack, auc);
fprintf('  mean d: front %+.2f  back %+.2f\n', mean(d(y==0)), mean(d(y==1)));

function d = decode(Xw, Weff, selIdx, mu, sd, w, b)
    nb = size(Xw,4); nWin = size(Xw,1); nc = size(Weff{1},2);
    F = zeros(nWin, nb*nc);
    for bb = 1:nb
        F(:, (bb-1)*nc + (1:nc)) = csp_features(Xw(:,:,:,bb), Weff{bb});
    end
    Fs = F(:, selIdx);
    d = ((Fs - mu)./sd) * w + b;
end
