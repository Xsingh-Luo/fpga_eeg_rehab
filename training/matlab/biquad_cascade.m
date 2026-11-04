function [y, dbg] = biquad_cascade(x, sos, mode, qfrac, qstate, accbits)
%BIQUAD_CASCADE 手写二阶段级联滤波（不依赖任何工具箱，直接对应 PL 实现）
%
%   [y, dbg] = biquad_cascade(x, sos, mode, qfrac, qstate, accbits)
%
%   x       : 输入列向量（或矩阵，按列处理）
%   sos     : (K x 6) 每行 [b0 b1 b2 a0 a1 a2]
%   mode    : 'float'（默认） | 'fixed'（定点整数逐位模拟）
%   qfrac   : 定点系数的小数位，默认 14（即 Q?.14）
%   qstate  : 定点状态的小数位，默认 15（即 Q?.15，与信号同域）
%   accbits : 定点累加器位宽，默认 48（饱和，不回绕）
%
%   dbg     : 结构体，定点模式下含每节最大状态绝对值（用于检查位宽是否够）
%
%   实现的是**转置直接 II 型**：
%       y[n] = b0*x[n] + s1
%       s1   = b1*x[n] - a1*y[n] + s2
%       s2   = b2*x[n] - a2*y[n]
%   反馈路径只有一次乘加，时序最容易收敛；不要换成直接 I 型。

if nargin < 3 || isempty(mode),     mode    = 'float'; end
if nargin < 4 || isempty(qfrac),    qfrac   = 14;        end
if nargin < 5 || isempty(qstate),   qstate  = 15;        end
if nargin < 6 || isempty(accbits),  accbits = 48;        end

if isvector(x), x = x(:); end
[n, nch] = size(x);
K = size(sos, 1);
y = zeros(n, nch);
dbg.maxstate = zeros(K, 1);

if strcmpi(mode, 'float')
    for c = 1:nch
        v = x(:, c);
        for k = 1:K
            b0 = sos(k,1); b1 = sos(k,2); b2 = sos(k,3);
            a1 = sos(k,5); a2 = sos(k,6);
            s1 = 0; s2 = 0; out = zeros(n,1);
            for i = 1:n
                out(i) = b0*v(i) + s1;
                s1 = b1*v(i) - a1*out(i) + s2;
                s2 = b2*v(i) - a2*out(i);
                dbg.maxstate(k) = max(dbg.maxstate(k), max(abs([s1 s2])));
            end
            v = out;
        end
        y(:, c) = v;
    end
else
    % ── 定点：系数与状态都按整数运算，累加器饱和 ──
    QS  = 2^qstate;
    lo  = -2^(accbits-1);
    hi  =  2^(accbits-1) - 1;
    Qc  = round(sos(:, 1:3) * 2^qfrac);
    Qa  = round(sos(:, 5:6) * 2^qfrac);
    for c = 1:nch
        v = round(x(:, c) * QS);          % 输入也量化到 Q?.qstate
        for k = 1:K
            b0 = Qc(k,1); b1 = Qc(k,2); b2 = Qc(k,3);
            a1 = Qa(k,1); a2 = Qa(k,2);
            s1 = 0; s2 = 0; out = zeros(n,1);
            for i = 1:n
                yn = sat(b0*v(i) + s1, qfrac, lo, hi);
                s1 = sat(b1*v(i) - a1*yn + s2, 1, lo, hi);
                s2 = sat(b2*v(i) - a2*yn,      1, lo, hi);
                out(i) = yn;
                dbg.maxstate(k) = max(dbg.maxstate(k), max(abs([s1 s2])));
            end
            v = out;
        end
        y(:, c) = v / QS;
    end
end
end

function y = sat(v, shift, lo, hi)
    y = floor(v / 2^shift);               % 算术右移，round-half-up
    if y > hi,  y = hi;  end
    if y < lo,  y = lo;  end
end
