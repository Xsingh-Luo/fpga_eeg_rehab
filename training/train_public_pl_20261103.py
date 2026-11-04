"""Retrain labelled B04 replay model for the ACTUAL PL global-six-feature definition.
Train-only parameter selection, then evaluate. Prior benchmark inspection means
the resulting report is developmental, not a fresh independent acceptance test.
"""
import sys,json,csv,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'validation/two_channel_20261102/pydeps'))
sys.path.insert(0,str(ROOT/'sim/reference'))
import numpy as np
from scipy.io import loadmat
from scipy import linalg
from sklearn.base import BaseEstimator,TransformerMixin
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.model_selection import StratifiedKFold
from train_teammate_two_channel import metrics
import verify_fullflow as ref
import gen_log2_lut as logs
from prepare_final_assets_20261103 import packet,model,projections,decision
OUT=ROOT/'validation/public_pl_model_20261103'

class PublicCSP(BaseEstimator,TransformerMixin):
    def __init__(self,reg=.2):self.reg=reg
    def fit(self,X,y):
        weights=[]
        for b in range(3):
            x=X[:,b]-X[:,b].mean(axis=1,keepdims=True)
            cov=np.einsum('nti,ntj->nij',x,x);cov/=np.trace(cov,axis1=1,axis2=2)[:,None,None]
            cls=[]
            for label in (0,1):
                c=cov[y==label].mean(axis=0);cls.append((1-self.reg)*c+self.reg*np.eye(3)*np.trace(c)/3)
            _,w=linalg.eigh(cls[0],cls[0]+cls[1]);weights.append(w[:,[0,-1]])
        weights=np.array(weights);weights*=.875/np.max(np.abs(weights))
        self.weights_=np.rint(weights*16384).astype(np.int32);return self
    def transform(self,X):
        z=np.floor(np.einsum('nbtc,bck->nbtk',X,self.weights_)/65536)
        v=np.var(z,axis=2).reshape(len(X),6)
        f=np.log(np.maximum(v,1e-30)/np.maximum(v.sum(axis=1,keepdims=True),1e-30))
        return (np.clip(np.rint(f*2048),-32768,32767)/2048)[:,[1,2,4,3]]

def pipe(reg,C):return Pipeline([('csp',PublicCSP(reg)),('scaler',StandardScaler()),('svm',SVC(kernel='linear',C=C))])
def filtered(X,q):
    out=np.empty((len(X),3,626,3))
    for i,trial in enumerate(X):
        for c in range(3):
            dc=ref.dc_run(trial[c,:876].tolist())
            for b in range(3):out[i,b,:,c]=ref.biquad(ref.biquad(dc,q[b,0].tolist()),q[b,1].tolist())[250:876]
    return out

def main():
    OUT.mkdir(exist_ok=False);data_path=ROOT/'matlab/data/subj04.mat';d=loadmat(data_path,squeeze_me=True)
    y=d['ytr'].astype(int);yt=d['yte'].astype(int)
    official=np.concatenate([loadmat(ROOT/f'data/raw/BCICIV_2b_true_labels/B04{s}E.mat',squeeze_me=True)['classlabel']-1 for s in ('04','05')])
    assert np.array_equal(yt,official),'Local MAT evaluation labels do not match official file order'
    q=np.array(json.loads((ROOT/'params/csp_svm_params.json').read_text(encoding='utf-8'))['iir']['sos_q'])
    X=filtered(d['Xtr'],q);Xe=filtered(d['Xte'],q)
    candidates=[];folds=list(StratifiedKFold(5,shuffle=True,random_state=20261103).split(X,y))
    for reg in (.05,.2,.5):
        for C in (.01,.1,1,10):
            scores=[float(pipe(reg,C).fit(X[tr],y[tr]).score(X[va],y[va])) for tr,va in folds]
            candidates.append({'reg':reg,'C':C,'mean':float(np.mean(scores))})
    best=max(candidates,key=lambda r:(r['mean'],-r['C'],r['reg']))
    fitted=pipe(best['reg'],best['C']).fit(X,y)
    weights=fitted.named_steps['csp'].weights_;sc=fitted.named_steps['scaler'];sv=fitted.named_steps['svm']
    w=sv.coef_[0]/sc.scale_;b=float(sv.intercept_[0]-w@sc.mean_)
    scale=min(1.,30000/(np.max(abs(w))*2048));folder=OUT/'models/dataset';folder.mkdir(parents=True)
    vals={'ff_sos.txt':q.ravel(),'ff_csp.txt':weights.ravel(),'ff_svmw.txt':np.rint(w*scale*2048).astype(int),'ff_svmb.txt':[round(b*scale*(1<<23))]}
    for name,v in vals.items():(folder/name).write_text(''.join(f'{int(x)}\n' for x in v),encoding='ascii')
    m=model(folder);logs.LUT=np.array([int(x,16) for x in (ROOT/'src/pl/log2_lut.hex').read_text().split()])
    decisions=[];replay=OUT/'dataset';replay.mkdir()
    with (replay/'index.csv').open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['trial','truth','file','expected_final_word'])
        for i,(trial,truth) in enumerate(zip(d['Xte'],yt)):
            x=trial[:,:876].T.astype(int);z=projections(x,m);decisions.append(decision(z,m))
            if i<20:
                name=f'trial_{i+1:03d}.bin';(replay/name).write_bytes(b''.join(packet(v,j) for j,v in enumerate(x)))
                value=decisions[-1]
                writer.writerow([i+1,int(truth),name,f'{((value&0xfffffffe)|int(value>=0)):08x}'])
    report={'training_trials':len(y),'source':str(data_path.relative_to(ROOT)),
        'source_sha256':hashlib.sha256(data_path.read_bytes()).hexdigest(),
        'model_compatibility_fix':'Global normalization over all six PL variances, fixed SELECT=[1,2,4,3], common CSP scale across bands; integer causal filters and final window250..875.',
        'official_eval_labels_match_local_mat':True,'selected_on_training_only':best,'search':candidates,
        'all320_integer_reference':metrics(yt,np.array(decisions)),
        'packaged20_integer_reference':metrics(yt[:20],np.array(decisions[:20])),
        'selection':'First20 in file order, no prediction-based cherry picking',
        'sessions_train':['01T','02T','03T'],'sessions_eval':['04E','05E'],
        'scope':'Development benchmark; prior dataset investigation and existing model development are disclosed. ARM/PL comparison on the new parameter bank is pending.',
        'samples_per_trial':876,'score_one_final_window_per_trial':True}
    (OUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (replay/'provenance.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with (OUT/'predictions.csv').open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['trial','truth','decision','prediction'])
        writer.writerows((i+1,int(t),int(dec),int(dec>=0)) for i,(t,dec) in enumerate(zip(yt,decisions)))
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
