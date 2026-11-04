function out = predict_record(varargin)
%PREDICT_RECORD 用本项目已部署的 3 通道 FBCSP+EA+SVM 模型识别一段 8 通道连续 EEG 记录
%
%   本脚本是"训练(train_final.m) → 导出(params/) → 新数据推理"链路的最后一段。
%   模型：BCI Competition IV 2b 被试 B04，3 子带 8-16/16-24/24-32 Hz + m=1 CSP
%         + 欧氏对齐 + 归一化对数方差 + MIBIF 选 4/6 维 + 线性 SVM。
%         判决：d = w'*((f-mu)./sd)+b , d>0 → 类 1 = 右手想象, d<0 → 类 0 = 左手想象。
%
%   用法（在 matlab/ 目录下）：
%     predict_record                                   % 默认读 ..\record\1.mat
%     predict_record('H:\FPGA2026QS\record\1.mat')
%     predict_record(RecordData)                       % 直接传工作区里的 RecordData 结构体
%     predict_record(..., 'channels',[1 2 3], 'strideSec',0.25)
%     out = predict_record(...)                        % 返回结果结构体
%
%   名字-值参数：
%     'channels'     参与模型的 3 个通道号（默认 [1 2 3]；按 规划/14 §2.1 CH1=C3,CH2=Cz,CH3=C4）
%     'winSec'       分析窗长，秒（默认 2.5 s = 626 点，对应 2b 的 [3.5,6.0] s）
%     'strideSec'    滑窗步长，秒（默认 0.5 s）
%     'eaCalWindows' 本段记录用于欧氏对齐校准的窗口数（默认 20，与项目部署口径一致）
%     'eaCalStartSec'校准/统计窗从记录第几秒开始（默认 3 s，跳过记录开头的滤波瞬态）
%     'subject'      模型被试编号（默认 4 = B04，与 params/ 里导出的参数一致）
%     'mode'         'both'(默认) | 'baked' | 'recalib'，见下
%     'outDir'       输出目录（默认 <项目根>/sessions/predict_record）
%     'makePlot'     是否画图存 PNG（默认 true）
%     'blocks'       已知真实标签的时间块 N×2（秒），如 [0 18.75; 18.75 37.5]
%     'blockLabels'  每块的类别（0=左手, 1=右手），如 [0 1]；给出后自动打印逐块正确率
%
%   两种推理口径：
%     baked   ：直接使用导出的折叠参数 W_eff（= 把这段数据回放给当前 FPGA 位流时
%               PL 实际会用的参数；W_eff 里已经含 B04 评价 session 的欧氏对齐矩阵）。
%     recalib ：按项目文档的部署口径，用本段记录自己的数据估计欧氏对齐矩阵 M_new，
%               再把训练域 CSP 滤波器 W 折叠成 W_eff_new = M_new' * W。W 由导出的
%               W_eff 反解得到（先撤销 B04 05E 的校准矩阵）；validate_pipeline.m 已用
%               BCI IV 2b 数据验证这条链路能复现 train_final 的 93.57% (B04 S2)。
%
%   ★ 关键前提：RTEEGAP 保存的记录里没有事件标记（RecordData.EventDescribe 为空），
%     所以无法按 MI 试次切分。这里用滑动窗逐段解码，再按窗口多数投票给出整段结论。
%     它适合"离线快速识别 / 链路自检"，不能替代带 cue 事件的正式评测。
%   ★ 8 通道 → 3 通道：模型 N_CH=3，只取 CH1–CH3（C3/Cz/C4），CH4–CH8 不参与运算。
%
%   输出：<outDir>/record_<名字>_windows.csv     逐窗决策值/标签
%         <outDir>/record_<名字>_result.mat      完整结果结构体
%         <outDir>/record_<名字>_decision.png    决策值时间曲线
%         <outDir>/record_<名字>_report.txt      UTF-8 中文报告
%
%   示例（命令行）：
%     matlab -batch "cd('H:\FPGA2026QS\matlab'); predict_record"
%
%   另见：validate_pipeline.m（验证本推理链路能复现 2b 上的官方精度）

% ---------------------------------------------------------------- 0. 参数
matlabDir = fileparts(mfilename('fullpath'));
root      = fileparts(matlabDir);
addpath(matlabDir);

cfg = struct();
cfg.matPath       = fullfile(root, 'record', '1.mat');
cfg.channels      = [1 2 3];
cfg.winSec        = 2.5;
cfg.strideSec     = 0.5;
cfg.eaCalWindows  = 20;
cfg.eaCalStartSec = 3.0;
cfg.subject       = 4;
cfg.mode          = 'both';
cfg.outDir        = fullfile(root, 'sessions', 'predict_record');
cfg.makePlot      = true;
cfg.printTable    = true;
cfg.blocks        = zeros(0, 2);   % [t0 t1] 秒；已知真实标签的时间块（可选）
cfg.blockLabels   = [];            % 每个块的真实类别（0=左手, 1=右手）

dst = [];
if nargin >= 1 && (ischar(varargin{1}) || isstring(varargin{1}))
    cfg.matPath = char(varargin{1});
    i0 = 2;
elseif nargin >= 1 && (isstruct(varargin{1}) || isnumeric(varargin{1}))
    dst = varargin{1};
    i0  = 2;
else
    i0 = 1;
end
for k = i0:2:numel(varargin)
    if ~ischar(varargin{k}), continue; end
    f = lower(char(varargin{k}));
    v = varargin{k+1};
    switch f
        case 'channels',       cfg.channels = v;
        case 'winsec',         cfg.winSec = v;
        case 'stridesec',      cfg.strideSec = v;
        case 'eacalwindows',   cfg.eaCalWindows = v;
        case 'eacalstartsec',  cfg.eaCalStartSec = v;
        case 'subject',        cfg.subject = v;
        case 'mode',           cfg.mode = lower(char(v));
        case 'outdir',         cfg.outDir = char(v);
        case 'makeplot',       cfg.makePlot = logical(v);
        case 'printtable',     cfg.printTable = logical(v);
        case 'blocks',         cfg.blocks = double(v);
        case 'blocklabels',    cfg.blockLabels = double(v);
        otherwise, warning('predict_record:未知参数 %s', f);
    end
end
if ~exist(cfg.outDir, 'dir'), mkdir(cfg.outDir); end

fprintf('==============================================================\n');
fprintf(' predict_record | 8ch record -> 3ch FBCSP+EA+SVM (project model)\n');
fprintf('==============================================================\n');

% ---------------------------------------------------------------- 1. 读数据
[X8, fs, recName, recInfo] = load_record(cfg.matPath, dst);
nCh = size(X8, 1);
if nCh < max(cfg.channels)
    error('记录只有 %d 个通道，无法取通道 %s', nCh, mat2str(cfg.channels));
end
X = X8(cfg.channels, :);
nSamp = size(X, 2);
durS = nSamp / fs;
fprintf(' file   : %s\n', recName);
fprintf(' shape  : %d x %d  (%.2f s @ %g Hz), channels used: %s\n', ...
        nCh, nSamp, durS, fs, mat2str(cfg.channels));
if nCh > 3
    fprintf(' note   : 模型 N_CH=3，CH%d-CH%d 不参与运算（规划/14 §2.1 / §5.4）\n', 4, nCh);
end
if abs(fs - 250) > 1e-6
    warning('采样率 %g Hz ~= 250 Hz，滤波器/SVM 按 250 Hz 训练，结果不可信', fs);
end
dinfo = data_quality(X8, X, fs, recInfo);
fprintf(' events : %s\n', dinfo.eventText);

% ---------------------------------------------------------------- 2. 读模型
GM   = load(fullfile(root, 'params', 'golden_vectors.mat'));
GOLD = GM.GOLD;
si   = find(cellfun(@(gg) double(gg.subj) == cfg.subject, GOLD), 1);
if isempty(si)
    error('golden_vectors.mat 里没有 subj=%d', cfg.subject);
end
g     = GOLD{si};
W_eff = g.W_eff;
mu    = g.mu(:).';  sd = g.sd(:).';  w = g.w(:);  b = double(g.b);
selIdx  = double(g.selIdx(:));
nBand   = numel(W_eff);
nComp   = size(W_eff{1}, 2);

F = load(fullfile(matlabDir, 'data', 'fbcsp_filters.mat'));
SOS3 = arrayfun(@(i) squeeze(F.sos3(i,:,:)), 1:size(F.sos3,1), 'uni', 0);
BANDS = F.bands3;
if size(BANDS,1) ~= nBand || any(abs(BANDS(:) - double(g.bands(:))) > 1e-9)
    error('滤波器组与模型 bands 不一致');
end
fprintf(' model  : B%02d (BCI IV 2b), %d bands, N_COMP=%d, FEAT_DIM=%d, selIdx=%s\n', ...
        cfg.subject, nBand, nComp, numel(selIdx), mat2str(selIdx.'));

% ---------------------------------------------------------------- 3. 预处理
Xc = X.' * double(g.meta_G);                 % uV -> counts（特征/EA 对整体缩放不敏感，保持同一口径）
a  = 1 / (1 + 2*pi*0.5/fs);                  % 0.5 Hz 一阶高通（与 prep_bands.m 相同）
Xd = filter([a -a], [1 -a], Xc, [], 1);      % 沿时间轴、连续滤波（对应在线部署）
T  = size(Xd, 1);
Bf = zeros(T, size(X,1), nBand);
for bb = 1:nBand
    Bf(:,:,bb) = biquad_cascade(Xd, SOS3{bb}, 'float');   % (T,nCh)，按列独立
end

winLen = round(cfg.winSec * fs) + 1;      % 2.5 s 窗 = 626 点（与 prep_bands 的 [3.5,6.0] s 一致）
stride = max(1, round(cfg.strideSec * fs));
if T < winLen, error('记录太短（%d 点），不足一个 %.2f s 分析窗', T, cfg.winSec); end
nWin   = floor((T - winLen) / stride) + 1;
starts = 1 + (0:nWin-1) * stride;             % 1-based
tSec   = (starts - 1) / fs;                   % 窗起点（秒）
Xw = zeros(nWin, size(X,1), winLen, nBand);
for bb = 1:nBand
    for kk = 1:nWin
        seg = Bf(starts(kk):starts(kk)+winLen-1, :, bb);          % (winLen,nCh)
        Xw(kk,:,:,bb) = (seg - mean(seg, 1)).';                    % 每窗每通道去均值
    end
end
fprintf(' window : %.2f s (%d pts) / stride %.2f s (%d pts) -> %d windows\n', ...
        cfg.winSec, winLen, cfg.strideSec, stride, nWin);

% ---------------------------------------------------------------- 4. 推理
R = struct();
if any(strcmp(cfg.mode, {'both','baked'}))
    [Fb, d] = decode_windows(Xw, W_eff, mu, sd, w, b, selIdx);
    R.baked = pack_result('baked', d, tSec, starts, nWin);
    R.baked.F = Fb;
    R.baked.postCalMask = tSec(:) >= cfg.eaCalStartSec;   % 剔除开头瞬态后的窗
    R.baked.scopeText  = sprintf('after startup (t >= %.1f s)', cfg.eaCalStartSec);
end
if any(strcmp(cfg.mode, {'both','recalib'}))
    % ---- 4a. 恢复训练域 CSP 滤波器 W（撤销 B04 05E 的 EA 折叠）----
    Wun = get_unfolded_W(root, matlabDir, g, SOS3, cfg);
    % ---- 4b. 用本段记录估计 M_new ----
    cand = find(tSec >= cfg.eaCalStartSec, 1, 'first');
    if isempty(cand), cand = 1; end
    calWindows = cand : min(nWin, cand + cfg.eaCalWindows - 1);
    nCal = numel(calWindows);
    Weff2 = cell(1, nBand);
    for bb = 1:nBand
        M = ea_matrix(Xw(calWindows, :, :, bb));
        Weff2{bb} = M.' * Wun{bb};
    end
    [Fr, d] = decode_windows(Xw, Weff2, mu, sd, w, b, selIdx);
    R.recalib = pack_result('recalib', d, tSec, starts, nWin);
    R.recalib.F = Fr;
    R.recalib.nCal = nCal;
    R.recalib.calWindows = calWindows;
    R.recalib.calEndSec = tSec(calWindows(end)) + cfg.winSec;
    R.recalib.postCalMask = tSec(:) > R.recalib.calEndSec;
    R.recalib.scopeText  = sprintf('post-cal (> %.1f s)', R.recalib.calEndSec);
end
if isfield(R, 'recalib'), R.calibrated = R.recalib; else, R.calibrated = R.baked; end

% ---------------------------------------------------------------- 5. 汇总
% 相邻滑窗有大量重叠，直接投票会把同一段数据重复计数；因此同时给出
% "独立窗"（步进 = 窗长，互不重叠）统计，最终结论以独立窗为准。
indep = false(nWin, 1);
indep(1:max(1, round(winLen/stride)):nWin) = true;

fprintf('\n--------------------------------------------------------------\n');
fprintf(' window-level decision (d>0 => class 1 = RIGHT hand)\n');
fprintf(' mode      scope                      n   RIGHT   frac   mean d   |d|max\n');
names = {};
if isfield(R, 'baked'),   names{end+1} = 'baked';   end
if isfield(R, 'recalib'), names{end+1} = 'recalib'; end
for ii = 1:numel(names)
    s = R.(names{ii});
    print_scope(s.mode, 'all sliding windows', s.d, true(size(s.d)));
    if ~isempty(s.postCalMask)
        print_scope(s.mode, s.scopeText, s.d, s.postCalMask);
        print_scope(s.mode, ['independent, ' s.scopeText], s.d, s.postCalMask & indep);
    end
end
if cfg.printTable
    fprintf('\n first/last windows of calibrated mode:\n');
    s = R.calibrated;
    fprintf('   t_start(s)  t_end(s)        d   label\n');
    showIdx = unique([1:min(6,numel(s.d)), max(1,numel(s.d)-3):numel(s.d)], 'stable');
    for kk = showIdx
        fprintf('   %8.2f   %8.2f  %+8.2f   %s\n', ...
                s.tSec(kk), s.tSec(kk)+cfg.winSec, s.d(kk), label_text(s.d(kk)>0));
    end
end

% 最终结论：calibrated 口径（同 session EA 校准），独立窗 + 校准窗之后的数据投票
s = R.calibrated;
maskF = s.postCalMask;
if sum(maskF & indep) >= 5
    maskF = maskF & indep;
elseif sum(maskF) < 3
    maskF = true(nWin, 1);
end
dd = s.d(maskF);
[finalLabel, fracRight, meanD, verdict, tendency] = aggregate_decision(dd);

% ---- 已知标签块评估（可选，用户给出真实标签时）----
blockEval = struct([]);
if ~isempty(cfg.blocks) && numel(cfg.blockLabels) == size(cfg.blocks,1)
    blockEval = struct();
    fprintf('\n known-label block evaluation (window center inside block):\n');
    for ii = 1:numel(names)
        r = R.(names{ii});
        B = eval_blocks(r.d, r.tSec, cfg.winSec, indep, cfg.blocks, cfg.blockLabels);
        B.mode = r.mode;
        blockEval.(r.mode) = B;
        fprintf('  [%s]\n', r.mode);
        for kk = 1:size(cfg.blocks,1)
            fprintf('    block %6.2f-%6.2f s  label=%s  n=%2d  acc=%5.1f%%  mean d=%+6.2f\n', ...
                    cfg.blocks(kk,1), cfg.blocks(kk,2), label_text(cfg.blockLabels(kk)>0), ...
                    B.perBlock(kk,1), 100*B.perBlock(kk,2), B.perBlock(kk,3));
        end
        fprintf('    overall: n=%d acc=%5.1f%%   independent: n=%d acc=%5.1f%%\n', ...
                B.n, 100*B.acc, B.nIndep, 100*B.accIndep);
    end
end

% ---- 适用性告警 ----
warnText = {};
if ~dinfo.hasEvents
    warnText{end+1} = '记录没有事件标记，无法按试次对齐，只能滑窗解码';
end
if isfinite(dinfo.maxAbsCorr) && dinfo.maxAbsCorr > 0.95
    warnText{end+1} = sprintf('CH1-CH3 最大相关 %.2f，共模/参考噪声可能主导', dinfo.maxAbsCorr);
end
if isfield(R, 'baked') && (max(abs(R.baked.d)) > 10 || abs(mean(R.baked.d)) > 5)
    warnText{end+1} = sprintf(['baked 口径 |d|max=%.1f、mean d=%+.1f，远超 2b 上的约 1-3：' ...
        '域不匹配严重，baked 标签不可信'], max(abs(R.baked.d)), mean(R.baked.d));
end
for kk = 1:numel(warnText)
    fprintf(' WARNING: %s\n', warnText{kk});
end
fprintf('\n==============================================================\n');
fprintf(' FINAL (%s mode, %d independent windows):\n', s.mode, numel(dd));
fprintf('   %s\n', verdict);
if ~isempty(tendency)
    fprintf('   %s\n', tendency);
end
fprintf('   RIGHT windows %d/%d = %.1f%%, mean d = %+.2f, |d|max = %.1f\n', ...
        sum(dd>0), numel(dd), 100*fracRight, meanD, max(abs(dd)));
fprintf('==============================================================\n');

% ---------------------------------------------------------------- 6. 输出
out = struct();
out.cfg = cfg;  out.record = recInfo;  out.quality = dinfo;
out.recName = recName;  out.nSamp = nSamp;
out.winLen = winLen;  out.stride = stride;  out.nWin = nWin;
out.tSec = tSec;  out.durS = durS;  out.fs = fs;
out.model = struct('subject', cfg.subject, 'bands', BANDS, 'selIdx', selIdx, ...
                   'mu', mu, 'sd', sd, 'w', w, 'b', b);
out.results = R;
out.indepMask = indep;
out.final = struct('mode', s.mode, 'label', finalLabel, 'verdict', verdict, ...
                   'tendency', tendency, 'fracRight', fracRight, 'meanD', meanD, ...
                   'nWindows', numel(dd));
out.final.warnings  = warnText;
out.final.blockEval = blockEval;
out.timestamp = datestr(now, 'yyyy-mm-dd HH:MM:SS');   %#ok<TNOW1,DATST>

safe = regexprep(recName, '[^\w\-]+', '_');
base = fullfile(cfg.outDir, ['record_' safe]);
save([base '_result.mat'], 'out', '-v7');
write_windows_csv([base '_windows.csv'], R, cfg.winSec);
write_report([base '_report.txt'], out);
if cfg.makePlot
    fig = plot_result(out);
    exportgraphics(fig, [base '_decision.png'], 'Resolution', 130);
    close(fig);
end
fprintf('\n outputs -> %s\n', cfg.outDir);
fprintf('   * _result.mat / _windows.csv / _report.txt');
if cfg.makePlot, fprintf(' / _decision.png'); end
fprintf('\n');
end % ===================== main =================================


% =======================================================================
%  局部函数
% =======================================================================
function [X8, fs, name, info] = load_record(src, dst)
%LOAD_RECORD 支持：.mat 路径 / RecordData 结构体 / 8xN 数值矩阵
    fs = 250;  info = struct();
    if ~isempty(dst) && isnumeric(dst)
        X8 = double(dst);  name = 'matrix';
        info.source = 'numeric matrix';
        info.hasEvents = false;  info.eventText = 'n/a (numeric matrix)';
        return;
    end
    if ~isempty(dst) && isstruct(dst)
        rd = dst;  name = 'workspace';
        if ~isfield(rd, 'NowData'), error('传入的结构体没有 NowData 字段'); end
        info.source = 'workspace RecordData struct';
    else
        matPath = src;
        if ~isfile(matPath), error('找不到记录文件: %s', matPath); end
        S = load(matPath);
        if ~isfield(S, 'RecordData')
            fn = fieldnames(S);
            num = fn(cellfun(@(f) isnumeric(S.(f)), fn));
            if numel(num) == 1
                X8 = double(S.(num{1}));  name = matPath;
                info.source = matPath;  info.hasEvents = false;
                info.eventText = 'n/a (plain matrix in .mat)';
                return;
            end
            error('%s 里没有 RecordData 结构体', matPath);
        end
        rd = S.RecordData;
        if numel(rd) > 1, rd = rd(1); end
        [~, b0, e0] = fileparts(matPath);  name = [b0 e0];
        info.source = matPath;
    end
    X8 = double(rd.NowData);
    if isfield(rd, 'RecFlag'), info.recordFlag = double(rd.RecFlag); end
    % 采样率：优先 Sample/Time，否则 250 Hz
    if isfield(rd, 'Sample') && isfield(rd, 'Time') && ~isempty(rd.Sample) ...
            && ~isempty(rd.Time) && double(rd.Sample) > 0 && double(rd.Time) > 0
        fs = double(rd.Sample) / double(rd.Time);
        if abs(fs - round(fs)) < 0.05, fs = round(fs); end
    end
    % 事件
    if isfield(rd, 'EventDescribe') && ~isempty(rd.EventDescribe)
        ev = rd.EventDescribe;
        if iscell(ev)
            info.eventText = sprintf('%d events: %s', numel(ev), strjoin(cellfun(@char, ev, 'uni', 0), ', '));
        else
            info.eventText = sprintf('%d events', numel(ev));
        end
        info.hasEvents = true;
    else
        info.hasEvents = false;
        info.eventText = 'none (EventDescribe empty) -> sliding-window decoding';
    end
    if isfield(rd, 'EventSample') && ~isempty(rd.EventSample)
        info.EventSample = double(rd.EventSample);
    end
    if isfield(rd, 'EventTime') && ~isempty(rd.EventTime)
        info.EventTime = double(rd.EventTime);
    end
end

function dInfo = data_quality(X8, X, fs, recInfo)
%DATA_QUALITY 简单质量统计（用于报告里的适用性判断）
    dInfo = struct();
    dInfo.source = recInfo.source;
    dInfo.eventText = recInfo.eventText;
    dInfo.hasEvents = recInfo.hasEvents;
    dInfo.fs = fs;
    dInfo.nCh = size(X8, 1);
    dInfo.durS = size(X8, 2) / fs;
    dInfo.std8 = std(X8, 0, 2);
    dInfo.maxAbs8 = max(abs(X8), [], 2);
    % 只用 10 s 之后的数据（避开开头滤波瞬态）估计带内功率与通道相关性
    i0 = min(size(X8,2), max(1, round(10*fs)));
    seg = X8(:, i0:end) - mean(X8(:, i0:end), 2);
    [Pb, fr] = pwelch_simple(seg, fs);
    bp = (fr >= 8) & (fr <= 30);
    dInfo.bandRms = sqrt(sum(Pb(:, bp), 2) * (fr(2)-fr(1)));   % 8-30 Hz RMS, uV
    dInfo.bandRmsSel = dInfo.bandRms(1:min(3, numel(dInfo.bandRms)));
    dInfo.selStd = std(X, 0, 2);
    if size(X,1) >= 2
        C = corrcoef(X.');
        C = C - diag(diag(C));
        dInfo.maxAbsCorr = max(abs(C(:)));
    else
        dInfo.maxAbsCorr = NaN;
    end
    dInfo.railCount = sum(sum(abs(X8) >= 32000));
end

function [P, f] = pwelch_simple(x, fs)
%PWELCH_SIMPLE 不依赖工具箱的 Welch PSD（Hann 窗，平均）
    n = size(x, 2);
    nfft = 512;
    if n < nfft, nfft = 2^floor(log2(max(n, 8))); end
    nover = floor(nfft/2);
    win = 0.5 - 0.5*cos(2*pi*(0:nfft-1)'/(nfft-1));
    step = nfft - nover;
    nSeg = max(1, floor((n - nover) / step));
    P = zeros(size(x,1), floor(nfft/2)+1);
    for k = 1:nSeg
        idx = (1:nfft) + (k-1)*step;
        if idx(end) > n, break; end
        Xs = x(:, idx);
        Xs = (Xs - mean(Xs, 2)) .* win.';
        Fx = fft(Xs, nfft, 2);
        P = P + abs(Fx(:, 1:floor(nfft/2)+1)).^2;
    end
    P = P / (nSeg * fs * sum(win.^2)) / 2;        % 单边谱密度
    P(:, 2:end-1) = 2*P(:, 2:end-1);
    f = (0:floor(nfft/2))' * fs / nfft;
end

function M = ea_matrix(X)
%EA_MATRIX 欧氏对齐白化矩阵 M = Rbar^(-1/2)（Rbar = 迹归一化协方差的算术平均）
%   X : (n_trials, n_ch, n_times)
    [n, nch, ~] = size(X);
    Rbar = zeros(nch);
    for i = 1:n
        Xi = squeeze(X(i,:,:));
        S  = Xi * Xi.';
        Rbar = Rbar + S / max(trace(S), 1e-30);
    end
    Rbar = Rbar / max(n, 1);
    Rbar = (Rbar + Rbar.') / 2;
    [U, D] = eig(Rbar);
    d = max(diag(D), 1e-12);
    M = U * diag(d.^ -0.5) * U.';
end

function [F, d] = decode_windows(Xw, Weff, mu, sd, w, b, selIdx)
%DECODE_WINDOWS 多子带特征 -> MIBIF 选择 -> 标准化 -> 线性 SVM 决策值
    nb = size(Xw, 4);
    nWin = size(Xw, 1);
    nc = size(Weff{1}, 2);
    F = zeros(nWin, nb*nc);
    for bb = 1:nb
        F(:, (bb-1)*nc + (1:nc)) = csp_features(Xw(:,:,:,bb), Weff{bb});
    end
    Fsel = F(:, selIdx);
    d = ((Fsel - mu) ./ sd) * w + b;
end

function r = pack_result(modeName, d, tSec, starts, nWin)
    r = struct();
    r.mode = modeName;
    r.d = d(:);
    r.tSec = tSec(:);
    r.starts = starts(:);
    r.nWin = nWin;
    r.label = double(d(:) > 0);
    r.fracRight = mean(r.label);
    r.meanD = mean(r.d);
    r.postCalMask = [];
    r.scopeText = 'all';
end

function [label, fracRight, meanD, verdict, tendency] = aggregate_decision(d)
%AGGREGATE_DECISION 窗口级多数投票给出整段结论（裕度 < 20% 判为"不可靠"）
    n = numel(d);
    fracRight = sum(d > 0) / max(n, 1);
    meanD = mean(d);
    margin = abs(2*fracRight - 1);       % 投票裕度：0 = 五五开，1 = 全票一致
    if fracRight >= 0.5, label = 1; else, label = 0; end
    tendency = '';
    if n < 3
        verdict = 'INSUFFICIENT WINDOWS';
    elseif margin < 0.2
        verdict = 'NO RELIABLE CLASS (window votes near chance)';
        if fracRight > 0.5
            tendency = sprintf('slight RIGHT tendency (%.1f%% right votes)', 100*fracRight);
        elseif fracRight < 0.5
            tendency = sprintf('slight LEFT tendency (%.1f%% right votes)', 100*fracRight);
        else
            tendency = 'perfectly split votes';
        end
    elseif label == 1
        if margin >= 0.6
            verdict = 'RIGHT hand imagery (class 1), high window-vote consistency';
        else
            verdict = 'RIGHT hand imagery (class 1)';
        end
    else
        if margin >= 0.6
            verdict = 'LEFT hand imagery (class 0), high window-vote consistency';
        else
            verdict = 'LEFT hand imagery (class 0)';
        end
    end
end

function B = eval_blocks(d, tSec, winSec, indep, blocks, labels)
%EVAL_BLOCKS 按"已知真实标签的时间块"评估逐窗判决（窗中心落在哪个块就归哪个块）
    mid = tSec(:) + winSec/2;
    y = nan(numel(d), 1);
    for k = 1:size(blocks,1)
        y(mid >= blocks(k,1) & mid < blocks(k,2)) = labels(k);
    end
    v  = ~isnan(y);
    vi = v & indep(:);
    B = struct('blocks', blocks, 'labels', labels(:), ...
               'n', sum(v), 'acc', mean((d(v)>0) == (y(v)==1)), ...
               'nIndep', sum(vi), 'accIndep', mean((d(vi)>0) == (y(vi)==1)), ...
               'perBlock', zeros(size(blocks,1), 3));
    for k = 1:size(blocks,1)
        m = v & (mid >= blocks(k,1) & mid < blocks(k,2));
        B.perBlock(k,:) = [sum(m), mean((d(m)>0) == (y(m)==1)), mean(d(m))];
    end
end

function print_scope(modeName, scope, d, mask)
    dd = d(mask);
    fprintf(' %-8s  %-26s %3d   %5d  %5.1f%%  %+7.2f   %6.1f\n', modeName, scope, ...
            numel(dd), sum(dd>0), 100*mean(dd>0), mean(dd), max(abs(dd)));
end

function s = label_text(isRight)
    if isRight, s = 'RIGHT(1)'; else, s = 'LEFT(0)'; end
end

function write_windows_csv(path, R, winSec)
    fid = fopen(path, 'w', 'n', 'UTF-8');
    if fid < 0, warning('无法写 %s', path); return; end
    fprintf(fid, 'mode,window,t_start_s,t_end_s,d,label,class\n');
    modes = fieldnames(R);
    modes = modes(ismember(modes, {'baked','recalib'}));
    for ii = 1:numel(modes)
        m = R.(modes{ii});
        for k = 1:numel(m.d)
            fprintf(fid, '%s,%d,%.3f,%.3f,%.6f,%d,%s\n', modes{ii}, k, ...
                    m.tSec(k), m.tSec(k)+winSec, m.d(k), m.label(k), label_text(m.label(k)>0));
        end
    end
    fclose(fid);
end

function write_report(path, out)
    fid = fopen(path, 'w', 'n', 'UTF-8');
    if fid < 0, warning('无法写 %s', path); return; end
    c = out.cfg;  q = out.quality;
    fprintf(fid, '================================================================\n');
    fprintf(fid, ' 8 通道连续 EEG 识别报告（本项目 FBCSP + 欧氏对齐 + 线性 SVM 模型）\n');
    fprintf(fid, '================================================================\n');
    fprintf(fid, '生成时间 : %s\n', out.timestamp);
    fprintf(fid, '数据来源 : %s\n', out.record.source);
    fprintf(fid, '记录形状 : %d 通道 x %d 点，%.2f s @ %g Hz\n', q.nCh, out.nSamp, out.durS, out.fs);
    fprintf(fid, '使用通道 : %s（CH1=C3, CH2=Cz, CH3=C4；N_CH=3 模型只用前 3 路）\n', mat2str(c.channels));
    fprintf(fid, '事件标记 : %s\n', q.eventText);
    fprintf(fid, '模型     : B%02d（BCI Competition IV 2b），3 子带 8-16/16-24/24-32 Hz，m=1\n', out.model.subject);
    fprintf(fid, '           归一化对数方差 + MIBIF(4/6) + 线性 SVM；d>0 => 类1 = 右手想象\n');
    fprintf(fid, '判决阈值 : d > 0 → 右手；d < 0 → 左手\n');
    fprintf(fid, '分析窗   : %.2f s（%d 点）/ 步长 %.2f s（%d 点），共 %d 窗\n', ...
            c.winSec, out.winLen, c.strideSec, out.stride, out.nWin);
    fprintf(fid, '\n---------------- 数据质量（适用性自检）----------------\n');
    fprintf(fid, '各通道(全部 8 路) 标准差 uV   : %s\n', mat2str(round(q.std8.', 2)));
    fprintf(fid, '各通道 8-30 Hz 带内 RMS uV    : %s\n', mat2str(round(q.bandRms.', 2)));
    fprintf(fid, '参与模型 3 路的 8-30Hz RMS uV: %s\n', mat2str(round(q.bandRmsSel.', 2)));
    if isfinite(q.maxAbsCorr)
        fprintf(fid, '前三路最大 |通道间相关系数|  : %.3f\n', q.maxAbsCorr);
    end
    fprintf(fid, '贴轨样本数(|x|>=32000)        : %d\n', q.railCount);
    fprintf(fid, '\n---------------- 识别结果 ----------------\n');
    modes = {'baked','recalib'};
    for ii = 1:numel(modes)
        if ~isfield(out.results, modes{ii}), continue; end
        r = out.results.(modes{ii});
        fprintf(fid, '[%s]  ', r.mode);
        switch r.mode
            case 'baked'
                fprintf(fid, '（直接用导出的 W_eff，等价于当前 FPGA 位流回放本段数据）\n');
            case 'recalib'
                fprintf(fid, '（用本段记录做欧氏对齐校准，再折叠 W_eff = M_new'' * W）\n');
        end
        fprintf(fid, '  全部 %d 窗 : 右手 %d 窗 (%.1f%%)，平均 d = %+.2f，中位 d = %+.2f，|d|max = %.1f\n', ...
                numel(r.d), sum(r.d>0), 100*mean(r.d>0), mean(r.d), median(r.d), max(abs(r.d)));
        if ~isempty(r.postCalMask)
            dd = r.d(r.postCalMask);
            if strcmp(r.mode, 'recalib')
                fprintf(fid, '  校准后 %d 窗: 右手 %d 窗 (%.1f%%)，平均 d = %+.2f\n', ...
                        numel(dd), sum(dd>0), 100*mean(dd>0), mean(dd));
                di = r.d(r.postCalMask & out.indepMask);
                fprintf(fid, '  校准后·独立窗 %d 个: 右手 %d 个 (%.1f%%)，平均 d = %+.2f\n', ...
                        numel(di), sum(di>0), 100*mean(di>0), mean(di));
                fprintf(fid, '  校准窗     : 第 %d-%d 窗（约 %.1f-%.1f s），共 %d 个\n', ...
                        r.calWindows(1), r.calWindows(end), ...
                        r.tSec(r.calWindows(1)), r.calEndSec, r.nCal);
            else
                fprintf(fid, '  剔除开头 %.1f s 后 %d 窗: 右手 %d 窗 (%.1f%%)，平均 d = %+.2f\n', ...
                        c.eaCalStartSec, numel(dd), sum(dd>0), 100*mean(dd>0), mean(dd));
            end
        end
    end
    if isfield(out.final, 'blockEval') && ~isempty(out.final.blockEval)
        fprintf(fid, '\n---------------- 真实标签块对照（用户提供）----------------\n');
        ms = fieldnames(out.final.blockEval);
        for ii = 1:numel(ms)
            B = out.final.blockEval.(ms{ii});
            fprintf(fid, '[%s]\n', B.mode);
            for kk = 1:size(B.blocks,1)
                fprintf(fid, '  块 %6.2f-%6.2f s  真实=%s  窗数 %2d  逐窗正确率 %5.1f%%  平均 d = %+6.2f\n', ...
                        B.blocks(kk,1), B.blocks(kk,2), label_text(B.labels(kk)>0), ...
                        B.perBlock(kk,1), 100*B.perBlock(kk,2), B.perBlock(kk,3));
            end
            fprintf(fid, '  合计: n=%d，逐窗正确率 %.1f%%；独立窗 n=%d，正确率 %.1f%%\n', ...
                    B.n, 100*B.acc, B.nIndep, 100*B.accIndep);
        end
        fprintf(fid, '  （窗中心落在哪个块就归哪个块；跨块边界的窗按中心归属）\n');
    end
    fprintf(fid, '\n---------------- 最终结论 ----------------\n');
    fprintf(fid, '%s\n', out.final.verdict);
    if isfield(out.final, 'tendency') && ~isempty(out.final.tendency)
        fprintf(fid, '%s\n', out.final.tendency);
    end
    fprintf(fid, '（mode=%s，独立窗 %d 个，右票 %.1f%%，平均决策值 %+.2f）\n', ...
            out.final.mode, out.final.nWindows, 100*out.final.fracRight, out.final.meanD);
    if isfield(out.final, 'warnings') && ~isempty(out.final.warnings)
        fprintf(fid, '\n适用性告警：\n');
        for kk = 1:numel(out.final.warnings)
            fprintf(fid, '  ! %s\n', out.final.warnings{kk});
        end
    end
    fprintf(fid, '\n---------------- 使用限制（务必阅读）----------------\n');
    fprintf(fid, '1. 这段记录没有事件标记，无法按 MI 试次对齐分析窗；本报告是滑窗解码，\n');
    fprintf(fid, '   不是官方精度口径，只能作为离线识别 / 链路自检。\n');
    fprintf(fid, '2. 模型在 BCI IV 2b B04 上训练（3 通道 C3/Cz/C4、不同被试与设备）。\n');
    fprintf(fid, '   按 规划/14 §5.4：不要指望直接套 2b 参数就有 2b 的准确率。\n');
    fprintf(fid, '3. baked 口径的决策值若明显超出训练分布（|d| 远大于 2b 上的约 1-3），\n');
    fprintf(fid, '   说明域不匹配严重；recalib 口径用本段记录做 EA 校准，更接近部署口径。\n');
    fprintf(fid, '4. 记录里 CH1-CH3 的 8-30Hz 功率、通道间相关性与频谱形状可用于判断\n');
    fprintf(fid, '   数据是否像运动想象 EEG；不像时，识别结果不应作为真实 BCI 决策使用。\n');
    fprintf(fid, '================================================================\n');
    fclose(fid);
end

function fig = plot_result(out)
    fig = figure('Visible', 'off', 'Position', [100 100 1150 520], 'Color', 'w');
    t = out.tSec;
    subplot(2,1,1); hold on; grid on;
    if isfield(out.results, 'baked')
        plot(t, out.results.baked.d, '-o', 'Color', [0.55 0.55 0.55], 'MarkerSize', 3, ...
             'DisplayName', 'baked (exported W_{eff})');
    end
    if isfield(out.results, 'recalib')
        plot(t, out.results.recalib.d, '-o', 'Color', [0.05 0.35 0.8], 'MarkerSize', 4, ...
             'DisplayName', 'recalib (EA on this record)');
        yl = ylim;
        cw = out.results.recalib;
        if ~isempty(cw.calWindows)
            patch([cw.tSec(cw.calWindows(1)) cw.calEndSec cw.calEndSec cw.tSec(cw.calWindows(1))], ...
                  [yl(1) yl(1) yl(2) yl(2)], [1 0.9 0.6], 'EdgeColor', 'none', ...
                  'FaceAlpha', 0.5, 'DisplayName', 'EA calibration');
        end
    end
    yline(0, 'k--', 'HandleVisibility', 'off');
    xlabel('window start time (s)'); ylabel('decision d');
    title(sprintf('record: %s   |   model B%02d, FINAL: %s', ...
          out.recName, out.model.subject, strtrim(out.final.verdict)), 'Interpreter', 'none');
    legend('Location', 'best'); xlim([0 max(t)+out.cfg.winSec]);
    subplot(2,1,2); hold on; grid on;
    modes = {'baked','recalib'};
    for ii = 1:numel(modes)
        if isfield(out.results, modes{ii})
            d = out.results.(modes{ii}).d;
            plot(t, movmean(d, 7), '-', 'LineWidth', 1.4, ...
                 'DisplayName', [modes{ii} ' (7-window moving mean)']);
        end
    end
    yline(0, 'k--', 'HandleVisibility', 'off');
    xlabel('window start time (s)'); ylabel('smoothed d'); legend('Location', 'best');
    xlim([0 max(t)+out.cfg.winSec]);
end

function Wun = get_unfolded_W(root, matlabDir, g, SOS3, cfg) %#ok<INUSD>
%GET_UNFOLDED_W 恢复训练域 CSP 滤波器 W（导出参数里只有 EA 折叠后的 W_eff）
%
%   原理：train_final.m 在评价 session 2（05E）上做 EA 校准后折叠
%         W_eff = M2' * W。这里用同一份 BCI 2b 数据（matlab/data/subj04.mat）
%         复现 M2，再反解 W = M2' \ W_eff。
%   结果缓存到 matlab/data/model_bXX_unfolded.mat，避免每次重算。
    % 注：matlab/data 目录在本机为只读，缓存放到可写的输出目录
    if ~exist(cfg.outDir, 'dir'), mkdir(cfg.outDir); end
    cache = fullfile(cfg.outDir, sprintf('model_b%02d_unfolded.mat', cfg.subject));
    if isfile(cache)
        C = load(cache);
        if isfield(C, 'Wun') && isequal(size(C.Wun{1}), size(g.W_eff{1}))
            Wun = C.Wun;
            return;
        end
    end
    f = fullfile(matlabDir, 'data', sprintf('subj%02d.mat', cfg.subject));
    if ~isfile(f)
        error(['恢复未折叠 CSP 权重需要 %s（BCI IV 2b 导出数据）。' ...
               '没有它只能用 baked 口径。'], f);
    end
    D = load(f);
    cfg2 = struct('tUse', [3.5 6.0], 'causal', true);
    Bte = prep_bands(D.Xte, D.meta, SOS3, cfg2);
    per = floor(numel(D.yte) / 2);           % 评价集两个 session 各 per 个试次
    Wun = cell(1, numel(SOS3));
    for bb = 1:numel(SOS3)
        [~, M2] = euclid_align(Bte(per+1:end, :, :, bb), 20);   % 05E 的 20 校准试次
        Wun{bb} = M2.' \ g.W_eff{bb};                            % W = inv(M2') * W_eff
    end
    save(cache, 'Wun', '-v7');
end
