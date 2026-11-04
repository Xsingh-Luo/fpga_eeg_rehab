"""Evaluate the frozen earlier-today candidate without fitting on the new labels."""
import sys
from pathlib import Path
import csv
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'validation/two_channel_20261102'
sys.path.insert(0, str(BASE / 'pydeps'))
sys.path.insert(0, str(ROOT / 'validation'))
import numpy as np
from scipy import signal
import audit_teammate_openbci as audit
import train_teammate_two_channel as train

OUT = ROOT / 'validation/fresh_openbci_20261102_2221'
MODEL = BASE / 'today_only/model_candidate.json'


def main():
    global OUT
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=OUT)
    args = parser.parse_args()
    OUT = args.data_root.resolve()
    audit.ROOT = OUT
    quality = audit.audit(OUT / '20261102/subj01_test20_meta.json')
    (OUT / 'signal_audit.json').write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding='utf-8')
    m = json.loads(MODEL.read_text())
    fresh_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted((OUT / '20261102').glob('*')) if p.is_file()}
    assert not set(fresh_hashes.values()) & set(m['input_hashes'].values()), 'Repeated training recording'
    q = np.asarray(m['sos_q14'])
    sos = np.zeros((3, 2, 6)); sos[:, :, :3] = q[:, :, :3] / 16384
    sos[:, :, 3] = 1; sos[:, :, 4:] = q[:, :, 3:] / 16384
    train.DATA = OUT
    report = {'protocol': 'Frozen earlier-today model; NO fitting, parameter search or channel selection on this file.',
              'model_sha256': hashlib.sha256(MODEL.read_bytes()).hexdigest(),
              'input_sha256': fresh_hashes,
              'mapping_assumption': 'Unchanged previously confirmed N3P=C3, N4P=C4; N7P=Cz excluded. New recording mapping not reconfirmed.',
              'actual_subject_identity_verified': False,
              'hardware_event_sync_verified': False,
              'board_parameters_changed': False,
              'float_reference_only': True}
    try:
        X, y, trials, hashes, counts, filtered, meta = train.load_session('20261102', 'subj01_test20', sos, notch50=True)
        transform = train.FBCSP(); transform.weights_ = np.asarray(m['csp_two_channel_weights'])
        features = transform.transform(X)
        d = features @ np.asarray(m['svm_folded_w']) + m['svm_folded_b']
        report['holdout'] = train.metrics(y, d)
        rows = [{**t, 'decision': float(v), 'prediction': int(v >= 0), 'correct': int((v >= 0) == t['label'])}
                for t, v in zip(trials, d)]
        with (OUT / 'frozen_predictions.csv').open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['trial','label','class','cue_sample','window_start',
                'window_stop_exclusive','decision','prediction','correct'], extrasaction='ignore')
            writer.writeheader(); writer.writerows(rows)
        epochs = np.stack([counts[t['window_start']:t['window_stop_exclusive']].astype(float) for t in trials])
        freq, psd = signal.welch(epochs, fs=250, nperseg=500, noverlap=250, axis=1, detrend='linear')
        def power(lo, hi):
            mask = (freq >= lo) & (freq <= hi)
            return np.trapezoid(psd[:, mask], x=freq[mask], axis=1)
        report['median_raw_50Hz_to_8_32_power_ratio_C3_C4'] = np.median(power(48,52)/np.maximum(power(8,32),1e-30),axis=0).tolist()
        report['status'] = 'Holdout evaluated once. This recording is now consumed for future tuning decisions.'
    except AssertionError as e:
        report['status'] = 'Quality gate rejected evaluation'
        report['reason'] = str(e)
    (OUT / 'frozen_evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for c in quality['channels']:
        print('CH%d rail=%.2f%% AC_rms=%.2fuV' % (c['channel'], c['stable_rail_pct'], c['median_1s_demeaned_rms_uV_assuming_metadata_gain']))


if __name__ == '__main__':
    main()
