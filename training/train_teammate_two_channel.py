"""C3=CH3/C4=CH4 subject-specific baseline with untouched session holdouts.

Run using validation/two_channel_20261102/pydeps, Python 3.13.
The channel labels in the recording metadata are deliberately ignored.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'validation/two_channel_20261102'
sys.path.insert(0, str(OUT / 'pydeps'))
import csv
import hashlib
import json
import numpy as np
from scipy import signal, linalg
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.model_selection import StratifiedKFold, GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix

DATA = ROOT / 'validation/teammate_openbci_20261102/openbci'
BANDS = [[8, 16], [16, 24], [24, 32]]
SELECT = [1, 2, 4, 3]  # Existing PL selection, zero-based; changing it requires RTL.
FS, WIN = 250, 626


class FBCSP(BaseEstimator, TransformerMixin):
    def __init__(self, reg=0.05):
        self.reg = reg

    def fit(self, X, y):
        # X: trials, bands, samples, physical channels C3/C4.
        self.weights_ = []
        for band in range(3):
            centered = X[:, band] - X[:, band].mean(axis=1, keepdims=True)
            cov = np.einsum('nti,ntj->nij', centered, centered)
            cov /= np.trace(cov, axis1=1, axis2=2)[:, None, None]
            cls = []
            for label in (0, 1):
                c = cov[y == label].mean(axis=0)
                cls.append((1-self.reg)*c + self.reg*np.eye(2)*np.trace(c)/2)
            _, W = linalg.eigh(cls[0], cls[0]+cls[1])
            # Bounded Q2.14 projection, include its quantization already in training.
            W /= np.max(np.abs(W))
            W = np.rint(W*16384).astype(np.int32) / 16384.0
            self.weights_.append(W)
        self.weights_ = np.asarray(self.weights_)
        return self

    def transform(self, X):
        z = np.einsum('nbtc,bck->nbtk', X, self.weights_)
        v = np.var(z, axis=2).reshape(len(X), 6)
        f = np.log(np.maximum(v, 1e-30) / np.maximum(v.sum(axis=1, keepdims=True), 1e-30))
        return f[:, SELECT]


def load_session(folder, stem, sos, notch50=False):
    meta_path = DATA / folder / f'{stem}_meta.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    assert meta['fs'] == FS
    csv_path = meta_path.with_name(meta['files']['raw_csv'])
    with csv_path.open(newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    counts = np.asarray([[int(r['ch3_count']), int(r['ch4_count'])] for r in rows], dtype=np.int32)
    seq = np.asarray([int(r['seq']) for r in rows])
    assert np.all(np.diff(seq) % 256 == 1)
    # DC blocker matches the configured transfer function, then quantized-coefficient
    # causal SOS filtering of the full recording (no per-trial reset, no future samples).
    a = 32361 / 32768
    values = counts.astype(float)
    if notch50:
        bn, an = signal.iirnotch(50, 30, fs=FS)
        values = signal.lfilter(bn, an, values, axis=0)
    dc = signal.lfilter([a, -a], [1, -a], values, axis=0)
    filtered = np.stack([signal.sosfilt(s, dc, axis=0) for s in sos])
    events_path = meta_path.with_name(meta['files']['events_csv'])
    with events_path.open(newline='', encoding='utf-8-sig') as f:
        events = list(csv.DictReader(f))
    ends = {(e['block'], e['trial']): e for e in events if e['event'] == 'imagery_end'}
    X, y, trials = [], [], []
    for event in events:
        if event['event'] != 'cue':
            continue
        label = int(event['label'])
        assert event['class'] == ('left' if label == 0 else 'right')
        end = ends[(event['block'], event['trial'])]
        cue = int(event['sample'])
        start, stop = cue + FS, cue + FS + WIN
        assert stop <= int(end['sample']) and stop <= len(counts)
        window = counts[start:stop]
        rail = ((window == -8388608) | (window == 8388607)).mean(axis=0)
        assert not np.any(rail), f'Saturated training/testing window: {events_path}, trial {event["trial"]}'
        X.append(filtered[:, start:stop])
        y.append(label)
        trials.append({'trial': int(event['trial']), 'label': label, 'class': event['class'],
                       'cue_sample': cue, 'window_start': start, 'window_stop_exclusive': stop,
                       'raw_rail_pct': (rail*100).tolist(),
                       'band_rms_uV_assuming_metadata_gain': (np.std(filtered[:, start:stop], axis=1)*meta['uv_per_count']).tolist()})
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (meta_path, csv_path, events_path)}
    return np.asarray(X), np.asarray(y), trials, hashes, counts, filtered, meta


def pipe(reg, C):
    return Pipeline([('csp', FBCSP(reg)), ('scaler', StandardScaler()),
                     ('svm', SVC(kernel='linear', C=C))])


def metrics(y, decision):
    pred = (decision >= 0).astype(int)
    cm = confusion_matrix(y, pred, labels=[0, 1])
    n = len(y)
    acc = float(accuracy_score(y, pred))
    # Wilson interval: small independent test sets warrant an uncertainty interval.
    z = 1.959963984540054
    center = (acc+z*z/(2*n))/(1+z*z/n)
    half = z*np.sqrt(acc*(1-acc)/n+z*z/(4*n*n))/(1+z*z/n)
    return {'trials': n, 'correct': int((pred == y).sum()), 'accuracy': acc,
            'balanced_accuracy': float(balanced_accuracy_score(y, pred)),
            'confusion_rows_truth_cols_prediction_left_right': cm.tolist(),
            'wilson_95_interval': [float(center-half), float(center+half)],
            'predicted_left': int((pred == 0).sum()), 'predicted_right': int((pred == 1).sum())}


def main():
    global OUT
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--notch50', action='store_true', help='Exploratory noise-control variant; old tests have already been evaluated')
    args = parser.parse_args()
    if args.notch50:
        OUT = OUT / 'notch50_exploratory'
    OUT.mkdir(parents=True, exist_ok=True)
    params = json.loads((ROOT/'params/csp_svm_params.json').read_text())
    q = np.asarray(params['iir']['sos_q'], dtype=np.int32)
    sos = np.zeros((3, 2, 6))
    sos[:, :, :3] = q[:, :, :3]/16384
    sos[:, :, 3] = 1
    sos[:, :, 4:] = q[:, :, 3:]/16384
    sessions = [load_session('20261031', 'subj01_s1_mi60', sos, args.notch50),
                load_session('20261031', 'subj01_test20', sos, args.notch50),
                load_session('20261102', 'subj01_test20', sos, args.notch50)]
    X, y = sessions[0][:2]
    assert len(y) == 60 and np.bincount(y).tolist() == [30, 30]
    folds = list(StratifiedKFold(5, shuffle=True, random_state=20261102).split(X, y))
    candidates = []
    for reg in (0.05, 0.2):
        for C in (0.1, 1.0, 10.0):
            scores = []
            for tr, va in folds:
                model = pipe(reg, C).fit(X[tr], y[tr])
                scores.append(float(model.score(X[va], y[va])))
            candidates.append({'reg': reg, 'C': C, 'mean_accuracy': float(np.mean(scores)), 'fold_scores': scores})
    # Deterministic tie-break favors smaller C, then stronger shrinkage.
    best = max(candidates, key=lambda c: (c['mean_accuracy'], -c['C'], c['reg']))
    blocked = []
    for tr, va in GroupKFold(5).split(X, y, groups=np.arange(len(y))//12):
        model = pipe(best['reg'], best['C']).fit(X[tr], y[tr])
        blocked.append(float(model.score(X[va], y[va])))
    model = pipe(best['reg'], best['C']).fit(X, y)
    evaluations = {}
    rows = []
    for index, name in enumerate(('training_60_resubstitution', 'holdout_20_20261031', 'holdout_20_20261102')):
        xs, ys, trials = sessions[index][:3]
        decision = model.decision_function(xs)
        evaluations[name] = metrics(ys, decision)
        for trial, d in zip(trials, decision):
            rows.append({'session': name, **trial, 'decision': float(d),
                         'prediction': int(d >= 0), 'correct': int((d >= 0) == trial['label'])})
    with (OUT/'trial_predictions.csv').open('w', newline='', encoding='utf-8') as f:
        fields = ['session','trial','label','class','cue_sample','window_start','window_stop_exclusive','decision','prediction','correct']
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader(); w.writerows(rows)
    csp = model.named_steps['csp'].weights_
    scaler, svm = model.named_steps['scaler'], model.named_steps['svm']
    w = svm.coef_[0] / scaler.scale_
    b = float(svm.intercept_[0] - np.dot(w, scaler.mean_))
    assert np.allclose(model.decision_function(X), model.named_steps['csp'].transform(X)@w+b)
    embedded = np.zeros((3, 3, 2))
    embedded[:, 0, :] = csp[:, 0, :]  # stream slot 0: C3
    embedded[:, 2, :] = csp[:, 1, :]  # stream slot 2: C4; Cz contribution zero
    model_export = {'version': 'two_channel_float_candidate_20261102', 'physical_channels': [3, 4],
                    'physical_sites': ['C3', 'C4'], 'fs': FS, 'window_samples': WIN,
                    'window_relative_to_cue_s': [1, 1+WIN/FS], 'bands': BANDS,
                    'feature_selection_zero_based': SELECT, 'causal_sos': sos.tolist(),
                    'dc_a': 32361/32768, 'csp_weights_bands_channels_components': csp.tolist(),
                    'embedded_three_slot_csp': embedded.tolist(), 'svm_folded_w': w.tolist(), 'svm_folded_b': b,
                    'standardization_train_mean': scaler.mean_.tolist(), 'standardization_train_scale': scaler.scale_.tolist(),
                    'class_mapping': {'0':'left', '1':'right'},
                    'causal_notch50_before_PL': args.notch50,
                    'deployment_status': 'OFFLINE_FLOAT_CANDIDATE_ONLY; integer reference and RTL comparison pending'}
    (OUT/'model_candidate.json').write_text(json.dumps(model_export, indent=2), encoding='utf-8')
    quality = []
    hashes = {}
    for name, session in zip(('train60', 'test20_old', 'test20_new'), sessions):
        xs, ys, trials, hs, counts, filtered, meta = session
        hashes.update(hs)
        quality.append({'session':name, 'trials':trials, 'conversion_gain_verified':False,
                        'hardware_markers':meta['hw_marker'], 'metadata_channel_names_ignored':True})
    report = {'protocol':'Train only 60 trials. Hyperparameters selected inside training; two complete 20-trial files untouched for selection.',
              'exploratory_reuse_of_previously_evaluated_tests':args.notch50,
              'causal_notch50_before_PL':args.notch50,
              'channels':{'C3':3,'C4':4,'Cz': 'excluded'}, 'bands':BANDS, 'window_samples':WIN,
              'float_filter_uses_PL_quantized_coefficients':True,
              'cv_candidates':candidates, 'selected_from_training_only':best,
              'blocked_cv_training_fold_scores':blocked, 'blocked_cv_training_mean':float(np.mean(blocked)),
              'evaluations':evaluations, 'quality':quality, 'input_sha256':hashes,
              'limitations':['Only one subject; not population accuracy.', 'Host event sample indices, no hardware marker synchronization.',
                             'No offline result is a board/RTL verification.', 'No artifact rejection based on test labels; no trial removed.',
                             'Cross-validation used for selection is not an unbiased final score.',
                             'Physical channel mapping confirmed by user; metadata site names are wrong.',
                             'Older session physical C3/C4 locations assumed unchanged as reported; confirm with teammate before scientific claims.']}
    (OUT/'training_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    np.savez_compressed(OUT/'epochs_and_labels.npz', train=X, train_y=y,
                        test_old=sessions[1][0], test_old_y=sessions[1][1],
                        test_new=sessions[2][0], test_new_y=sessions[2][1])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(9, 4), constrained_layout=True)
    for ax, name in zip(axes, ('holdout_20_20261031', 'holdout_20_20261102')):
        m = evaluations[name]
        cm = np.asarray(m['confusion_rows_truth_cols_prediction_left_right'])
        ax.imshow(cm, vmin=0, vmax=10, cmap='Blues')
        for row in range(2):
            for col in range(2):
                ax.text(col,row,str(cm[row,col]),ha='center',va='center',color='white' if cm[row,col]>5 else 'black',fontsize=20)
        ax.set(xticks=[0,1],yticks=[0,1],xticklabels=['Left','Right'],yticklabels=['Left','Right'],
               xlabel='Prediction',ylabel='True cue', title=f'{name[-8:]}: {m["correct"]}/20 ({m["accuracy"]:.0%})')
    fig.suptitle('C3 / CH3 + C4 / CH4: untouched session tests')
    if args.notch50:
        fig.suptitle('Exploratory 50 Hz notch control: previously evaluated test files')
    fig.savefig(OUT/'holdout_confusion.png',dpi=170)
    print(json.dumps({'selected':best,'blocked_cv_mean':np.mean(blocked),'evaluations':evaluations},indent=2))


if __name__ == '__main__':
    main()
