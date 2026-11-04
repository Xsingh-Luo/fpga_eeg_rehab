"""Quantization fidelity on fitting recordings, explicitly not generalization accuracy."""
from pathlib import Path
import sys,json,csv
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'validation/two_channel_20261102/pydeps'),str(ROOT/'sim/reference')]
import numpy as np
from scipy import signal
import gen_log2_lut as logs
from prepare_final_assets_20261103 import model,projections,decision
from train_teammate_two_channel import FBCSP,metrics
import train_teammate_two_channel as train

def main():
    r=ROOT;c=json.loads((r/'validation/model_final_20261103/candidate.json').read_text(encoding='utf-8'))
    m=model(r/'validation/final_assets_20261103/models/live')
    logs.LUT=np.array([int(s,16) for s in (r/'zynq/pl/synth/log2_lut.hex').read_text().split()])
    q=m[0];sos=np.zeros((3,2,6));sos[:,:,:3]=q[:,:,:3]/16384;sos[:,:,3]=1;sos[:,:,4:]=q[:,:,3:]/16384;rows=[]
    folders=[r/'validation/teammate_openbci_20261102/openbci',r/'validation/fresh_openbci_20261102_2221',r/'validation/fresh_openbci_20261102_batch2']
    for session,folder in enumerate(folders):
        train.DATA=folder;X,y,trials,_,counts,*_=train.load_session('20261102','subj01_test20',sos,True)
        b,a=signal.iirnotch(50,30,fs=250);v=signal.lfilter(b,a,counts,axis=0)
        v=np.clip(np.where(v>=0,np.floor(v+.5),np.ceil(v-.5)),-8388608,8388607).astype(int)
        x=np.zeros((len(v),3),dtype=int);x[:,0]=v[:,0];x[:,2]=v[:,1];z=projections(x,m)
        transformer=FBCSP();transformer.weights_=np.array(c['csp_weights'])
        fd=transformer.transform(X)@np.array(c['svm_w'])+c['svm_b']
        for truth,t,d in zip(y,trials,fd):
            integer=decision(z[:t['window_stop_exclusive']],m)
            rows.append({'recording':session,'trial':t['trial'],'truth':int(truth),'float_decision':float(d),
                         'integer_decision':integer,'label_agrees':int((d>=0)==(integer>=0))})
    report={'scope':'Quantization fidelity on fitting data only; not held-out accuracy',
       'label_agreement':sum(x['label_agrees'] for x in rows),'trials':len(rows),
       'integer_resubstitution':metrics(np.array([x['truth'] for x in rows]),np.array([x['integer_decision'] for x in rows]))}
    (r/'validation/model_final_20261103/integer_fidelity.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with (r/'validation/model_final_20261103/integer_predictions.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
