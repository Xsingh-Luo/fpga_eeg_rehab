"""Session-held-out exploratory optimization on all three now-consumed recordings.
Calibration uses only the pre-first-cue interval; no validation label fits.
"""
import sys, json, csv, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'validation/two_channel_20261102'
sys.path.insert(0,str(BASE/'pydeps'))
import numpy as np
from scipy import signal
from sklearn.model_selection import LeaveOneGroupOut
from train_teammate_two_channel import pipe, metrics, FBCSP
OUT=ROOT/'validation/model_final_20261103'
CASES=[ROOT/'validation/teammate_openbci_20261102/openbci/20261102',
       ROOT/'validation/fresh_openbci_20261102_2221/20261102',
       ROOT/'validation/fresh_openbci_20261102_batch2/20261102']

def load(folder,q):
    meta=json.loads((folder/'subj01_test20_meta.json').read_text(encoding='utf-8'))
    with (folder/meta['files']['raw_csv']).open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    counts=np.array([[int(r['ch3_count']),int(r['ch4_count'])] for r in rows],dtype=float)
    seq=np.array([int(r['seq']) for r in rows]);assert np.all(np.diff(seq)%256==1)
    with (folder/meta['files']['events_csv']).open(encoding='utf-8-sig',newline='') as f: events=list(csv.DictReader(f))
    cues=[e for e in events if e['event']=='cue'];first=int(cues[0]['sample'])
    assert first-750>=500,'Need at least 2s pre-cue calibration after 3s settling'
    bn,an=signal.iirnotch(50,30,fs=250)
    dc=signal.lfilter([32361/32768,-32361/32768],[1,-32361/32768],signal.lfilter(bn,an,counts,axis=0),axis=0)
    sos=np.zeros((3,2,6));sos[:,:,:3]=q[:,:,:3]/16384;sos[:,:,3]=1;sos[:,:,4:]=q[:,:,3:]/16384
    filtered=np.stack([signal.sosfilt(s,dc,axis=0) for s in sos])
    cal=filtered[:,750:first];cal=cal-cal.mean(axis=1,keepdims=True)
    cov=np.einsum('btc,btd->cd',cal,cal)/(3*cal.shape[1])
    # Shrink the label-free calibration covariance to avoid unstable inverses.
    cov=.9*cov+.1*np.eye(2)*np.trace(cov)/2
    ev,U=np.linalg.eigh(cov)
    maps={'none':np.eye(2),'diagonal':np.diag(1/np.sqrt(np.diag(cov))),
          'whitening':(U*(1/np.sqrt(np.maximum(ev,1e-12))))@U.T}
    X=[];y=[];ids=[]
    for e in cues:
        a=int(e['sample'])+250;b=a+626
        assert b<=len(counts)
        assert not np.any((counts[a:b]==-8388608)|(counts[a:b]==8388607))
        X.append(filtered[:,a:b]);y.append(int(e['label']));ids.append(int(e['trial']))
    return np.array(X),np.array(y),maps,{'source':str(folder.relative_to(ROOT)),
        'created':meta['created'],'calibration_start':750,'calibration_stop_exclusive':first,
        'calibration_matrices':{k:v.tolist() for k,v in maps.items()},'trials':ids,
        'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*') if p.is_file()}}

def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'report.json').exists():raise FileExistsError('Preserve completed model report')
    q=np.array(json.loads((ROOT/'params/csp_svm_params.json').read_text(encoding='utf-8'))['iir']['sos_q'])
    loaded=[load(p,q) for p in CASES]
    y=np.concatenate([r[1] for r in loaded]);groups=np.repeat(np.arange(3),[len(r[1]) for r in loaded])
    variants={name:np.concatenate([np.einsum('nbtc,cd->nbtd',r[0],r[2][name]) for r in loaded]) for name in ('none','diagonal','whitening')}
    candidates=[(name,reg,C) for name in variants for reg in (.05,.2,.5) for C in (.01,.1,1.,10.)]
    def choose(idx):
        results=[]
        for name,reg,C in candidates:
            ds=[]
            for tr,va in LeaveOneGroupOut().split(idx,y[idx],groups[idx]):
                model=pipe(reg,C).fit(variants[name][idx[tr]],y[idx[tr]])
                ds.append(float(model.score(variants[name][idx[va]],y[idx[va]])))
            results.append({'calibration':name,'reg':reg,'C':C,'mean':float(np.mean(ds)),'scores':ds})
        return max(results,key=lambda r:(r['mean'],-r['C'],r['reg'])),results
    decisions=np.zeros(len(y));outer=[]
    for tr,va in LeaveOneGroupOut().split(groups,y,groups):
        best,_=choose(tr);name=best['calibration']
        model=pipe(best['reg'],best['C']).fit(variants[name][tr],y[tr])
        decisions[va]=model.decision_function(variants[name][va])
        outer.append({'held_out_recording':int(groups[va][0]),'selected_inside_training':best,'metrics':metrics(y[va],decisions[va])})
        print('OUTER',outer[-1],flush=True)
    best,search=choose(np.arange(len(y)));name=best['calibration']
    fitted=pipe(best['reg'],best['C']).fit(variants[name],y)
    scaler=fitted.named_steps['scaler'];svm=fitted.named_steps['svm'];w=svm.coef_[0]/scaler.scale_
    b=float(svm.intercept_[0]-w@scaler.mean_)
    report={'protocol':'Nested leave-one-recording-out; all three recordings previously inspected, so exploratory, not a new independent test.',
       'mapping_assumed':{'C3':3,'C4':4},'subject_identity_verified':False,'excluded_yesterday':True,
       'calibration':'Only sample750 to first cue, no cue labels; fixed before classified trials.',
       'outer':outer,'combined':metrics(y,decisions),'final_selection':best,'search':search,
       'sessions':[r[3] for r in loaded],'new_recording_required_for_final_acceptance':True}
    candidate={'version':'multisession_candidate_20261103','fs':250,'physical_channels':[3,4],
       'notch_hz':50,'notch_Q':30,'dc_a_q15':32361,'sos_q14':q.tolist(),'window':626,
       'feature_selection':[1,2,4,3],'calibration':name,'calibration_shrinkage':.1,
       'csp_weights':fitted.named_steps['csp'].weights_.tolist(),'svm_w':w.tolist(),'svm_b':b,
       'labels':{'0':'left','1':'right'},'scope':'Exploratory candidate, not automatically deployed',
       'training_sources':[r[3] for r in loaded]}
    (OUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (OUT/'candidate.json').write_text(json.dumps(candidate,indent=2),encoding='utf-8')
    with (OUT/'nested_predictions.csv').open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['recording','trial','truth','decision','prediction'])
        for i,(truth,d) in enumerate(zip(y,decisions)):writer.writerow([groups[i],i%20,truth,float(d),int(d>=0)])
    print('FINAL',json.dumps(report['combined']),best,flush=True)

if __name__=='__main__':main()
