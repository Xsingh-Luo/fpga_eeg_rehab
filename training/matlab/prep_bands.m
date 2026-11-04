function Xb = prep_bands(Xraw, meta, sosCell, cfg)
%PREP_BANDS 去DC（全带共用）→ 逐带带通 → 裁剪到分析窗 → 每 trial 去均值
%
%   Xraw    : (n_trials, n_ch, T_raw) 原始 int16 计数域
%   meta    : 含 fs / G / win_export
%   sosCell : 1×B cell，每个子带的 SOS（n_sec × 6）
%   cfg     : 需含 tUse（[t0 t1] 秒）与 causal（true=因果 / false=零相位）
%
%   返回 Xb : (n_trials, n_ch, T_win, B)

n  = size(Xraw, 1);
fs = meta.fs;
X  = double(Xraw) / meta.G;

% 去 DC：一阶高通 0.5 Hz
a = 1 / (1 + 2*pi*0.5/fs);
X = filter([a -a], [1 -a], X, [], 3);

i0 = round((cfg.tUse(1) - meta.win_export(1)) * fs) + 1;
i1 = i0 + round(diff(cfg.tUse) * fs);

B  = numel(sosCell);
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
    Xb(:, :, :, b) = Xc - mean(Xc, 3);      % 每 trial 每通道去均值
end
end
