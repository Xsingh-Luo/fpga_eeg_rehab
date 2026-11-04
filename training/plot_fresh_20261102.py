"""Signal-only cross-session comparison and frozen holdout decisions."""
from pathlib import Path
import sys
import csv
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'validation/two_channel_20261102/pydeps'))
import numpy as np
from scipy import signal
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT = ROOT / 'validation/fresh_openbci_20261102_2221'
OLD = ROOT / 'validation/teammate_openbci_20261102/openbci/20261102'
fig, ax = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
stats = []
for folder, name in ((OLD, 'Earlier today (training)'), (OUT / '20261102', '22:08 fresh holdout')):
    with (folder / 'subj01_test20_raw.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    x = np.array([[int(r['ch3_count']), int(r['ch4_count'])] for r in rows])[750:] * 0.022352
    f, psd = signal.welch(x, fs=250, nperseg=1000, axis=0)
    for c, label in enumerate(('C3 / CH3', 'C4 / CH4')):
        ax[0,c].semilogy(f, psd[:,c], label=name)
        ax[0,c].set(xlim=(1,65), xlabel='Frequency (Hz)', ylabel='PSD (uV^2/Hz)', title=label)
        ax[0,c].axvspan(8,32, color='green', alpha=.07)
    n = len(x)//250
    blocks = x[:n*250].reshape(n,250,2)
    rms = np.std(blocks,axis=1)
    ax[1,0].plot(np.arange(n), rms[:,0], label=name+' C3')
    ax[1,0].plot(np.arange(n), rms[:,1], linestyle='--', label=name+' C4')
    stats.append({'recording':name, 'median_1s_AC_rms_uV_C3_C4':np.median(rms,axis=0).tolist()})
with (OUT / 'frozen_predictions.csv').open(newline='') as f:
    preds = list(csv.DictReader(f))
decisions = np.array([float(p['decision']) for p in preds])
ax[1,1].bar(np.arange(1,len(preds)+1),decisions,color=['#3976ac' if p['label']=='0' else '#dc9245' for p in preds])
ax[1,1].axhline(0,color='black',lw=1)
ax[1,1].set(title='Frozen model: all 20 decisions > 0 (right)',xlabel='Trial (blue=left cue, orange=right cue)',ylabel='SVM decision')
ax[1,0].set(title='Per-second signal variation (DC removed)',xlabel='Time after 3s startup (s)',ylabel='RMS (uV)')
for a in ax.flat:
    a.grid(alpha=.2)
for a in (ax[0,0],ax[0,1],ax[1,0]): a.legend(fontsize=8)
fig.suptitle('Assumed unchanged CH3=C3 / CH4=C4; gain from metadata; no model refit')
fig.savefig(OUT/'signal_comparison.png',dpi=150)
(OUT/'comparison_stats.json').write_text(json.dumps(stats,indent=2),encoding='utf-8')
print(json.dumps(stats,indent=2))
