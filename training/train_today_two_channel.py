"""Exploratory nested CV using ONLY the corrected-montage 20261102 recording."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'validation/two_channel_20261102'
sys.path.insert(0,str(BASE/'pydeps'))
import csv
import json
import numpy as np
from sklearn.model_selection import StratifiedKFold, GroupKFold
from train_teammate_two_channel import load_session, pipe, metrics

OUT=BASE/'today_only'
CANDIDATES=[(reg,C) for reg in (0.05,0.2) for C in (0.1,1.,10.)]


def choose(X,y,seed):
    folds=list(StratifiedKFold(3,shuffle=True,random_state=seed).split(X,y))
    rows=[]
    for reg,C in CANDIDATES:
        scores=[float(pipe(reg,C).fit(X[tr],y[tr]).score(X[va],y[va])) for tr,va in folds]
        rows.append({'reg':reg,'C':C,'score':float(np.mean(scores))})
    return max(rows,key=lambda r:(r['score'],-r['C'],r['reg'])),rows


def crossvalidate(X,y,folds,seed):
    decisions=np.zeros(len(y)); selected=[]
    for fold,(tr,va) in enumerate(folds):
        best,_=choose(X[tr],y[tr],seed+fold)
        model=pipe(best['reg'],best['C']).fit(X[tr],y[tr])
        decisions[va]=model.decision_function(X[va])
        selected.append({'fold':fold,'train_trials':tr.tolist(),'validation_trials':va.tolist(),'selected':best})
    return decisions,selected


def main():
    OUT.mkdir(exist_ok=True)
    old=json.loads((ROOT/'params/csp_svm_params.json').read_text())
    q=np.asarray(old['iir']['sos_q'])
    sos=np.zeros((3,2,6)); sos[:,:,:3]=q[:,:,:3]/16384; sos[:,:,3]=1; sos[:,:,4:]=q[:,:,3:]/16384
    report={'protocol':'Today only, 20 trials. Nested trial-level CV is exploratory, NOT an independent recording test.',
            'excluded':'ALL 20261031 recordings: user says yesterday electrode positions were inaccurate.',
            'physical_channels':{'C3':3,'C4':4,'Cz':'CH7 excluded'}, 'fresh_test_required':True, 'variants':{}}
    predictions=[]
    for notch,name in ((True,'primary_notch50'),(False,'comparison_no_notch')):
        X,y,trials,hashes,*_=load_session('20261102','subj01_test20',sos,notch50=notch)
        assert len(y)==20 and np.bincount(y).tolist()==[10,10]
        repeats=[]
        first=None
        for repeat in range(5):
            seed=20261102+repeat
            folds=list(StratifiedKFold(5,shuffle=True,random_state=seed).split(X,y))
            d,folds_used=crossvalidate(X,y,folds,seed)
            result=metrics(y,d)
            repeats.append(result['accuracy'])
            if repeat==0:
                first={'metrics':result,'folds':folds_used}
                for trial,decision in zip(trials,d):
                    predictions.append({'variant':name,'trial':trial['trial'],'label':trial['label'],
                                        'decision':float(decision),'prediction':int(decision>=0),
                                        'correct':int((decision>=0)==trial['label'])})
        blocked_d,blocked_folds=crossvalidate(X,y,list(GroupKFold(5).split(X,y,groups=np.arange(20)//4)),20261102)
        report['variants'][name]={'first_nested_5fold':first,'repeated_nested_5fold_accuracy':repeats,
                                  'repeated_mean':float(np.mean(repeats)),'repeated_std':float(np.std(repeats)),
                                  'repeats_are_same_20_trials_not_100_independent_trials':True,
                                  'blocked_nested_cv':metrics(y,blocked_d),'blocked_folds':blocked_folds}
        if notch:
            best,search=choose(X,y,20261102)
            final=pipe(best['reg'],best['C']).fit(X,y)
            W=final.named_steps['csp'].weights_
            scaler=final.named_steps['scaler']; svm=final.named_steps['svm']
            w=svm.coef_[0]/scaler.scale_
            b=float(svm.intercept_[0]-w@scaler.mean_)
            embedded=np.zeros((3,3,2)); embedded[:,0,:]=W[:,0,:]; embedded[:,2,:]=W[:,1,:]
            export={'physical_input_channels':[3,4], 'training_only_file':'20261102/subj01_test20',
                    'fs':250,'win_len':626,'causal_notch_hz':50,'notch_Q':30,'dc_a_q15':32361,
                    'sos_q14':q.tolist(),'feature_selection':[1,2,4,3],
                    'csp_two_channel_weights':W.tolist(),'embedded_three_slot_weights':embedded.tolist(),
                    'svm_folded_w':w.tolist(),'svm_folded_b':b,'labels':{'0':'left','1':'right'},
                    'selection_on_all_20':best,'input_hashes':hashes,
                    'deployment_status':'OFFLINE_ONLY; fresh corrected-montage test, integer verification and PS notch implementation required'}
            (OUT/'model_candidate.json').write_text(json.dumps(export,indent=2),encoding='utf-8')
            report['final_fit_parameter_search']=search
            report['input_hashes']=hashes
    with (OUT/'nested_predictions.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(predictions[0])); writer.writeheader(); writer.writerows(predictions)
    (OUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({name:{'first':r['first_nested_5fold']['metrics'],'repeat_mean':r['repeated_mean'],
                           'blocked':r['blocked_nested_cv']} for name,r in report['variants'].items()},indent=2))


if __name__=='__main__':main()
