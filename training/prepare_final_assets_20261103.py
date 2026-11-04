"""Export real labelled replay trials and quantized live parameters, with reference checks."""
from pathlib import Path
import sys,json,hashlib,csv,shutil
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'validation/two_channel_20261102/pydeps'))
sys.path.insert(0,str(ROOT/'sim/reference'))
import numpy as np
from scipy.io import loadmat
import verify_fullflow as ref
import gen_log2_lut as logs
from train_teammate_two_channel import metrics
OUT=ROOT/'validation/final_assets_20261103'

def model(folder):
    def values(name):return np.array([int(x) for x in (folder/name).read_text().split()],dtype=np.int64)
    return values('ff_sos.txt').reshape(3,2,5),values('ff_csp.txt').reshape(3,3,2),values('ff_svmw.txt'),int(values('ff_svmb.txt')[0])

def projections(x,m):
    sos,W,_,_=m;n=len(x);bands=np.zeros((n,3,3),dtype=np.int64)
    for c in range(3):
        if not np.any(x[:,c]):continue
        dc=ref.dc_run(x[:,c].tolist())
        for b in range(3):
            bands[:,b,c]=ref.biquad(ref.biquad(dc,sos[b,0].tolist()),sos[b,1].tolist())
    return np.clip(np.einsum('nbc,bck->nbk',bands,W)>>16,-8388608,8388607).reshape(n,6)

def decision(z,m):
    z=z[-626:];assert len(z)==626
    f=np.array(logs.lognorm_ref(z.sum(axis=0).tolist(),(z*z).sum(axis=0).tolist(),626))
    return ref.sat(int(f[[1,2,4,3]]@m[2])*2+m[3],32)

def packet(values,seq):
    data=bytearray([0xa0,seq%256])
    for v in list(values)+[0]*5:data.extend(int(v).to_bytes(3,'big',signed=True))
    data.extend(bytes(6));data.append(0xc0);assert len(data)==33;return data

def main():
    OUT.mkdir(exist_ok=False)
    logs.LUT=np.array([int(x,16) for x in (ROOT/'src/pl/log2_lut.hex').read_text().split()])
    baseline=ROOT/'validation/bci_deploy/bundle';m=model(baseline)
    raw=(baseline/'sample_stream.bin').read_bytes()
    x=np.array([[int.from_bytes(raw[i+2+3*c:i+5+3*c],'big',signed=True) for c in range(3)] for i in range(0,len(raw),33)])
    z=projections(x,m);expected=[int(w,16) for w in (baseline/'expected_decision_words.txt').read_text().split()]
    for i in range(625,len(x)):
        d=decision(z[:i+1],m);assert ((d&0xfffffffe)|int(d>=0))==expected[i],i
    print('REFERENCE_MATCH_119_PASS',flush=True)
    live=OUT/'models/live';dataset=OUT/'models/dataset';live.mkdir(parents=True);dataset.mkdir(parents=True)
    for p in baseline.glob('ff_*.txt'):shutil.copy2(p,dataset/p.name)
    candidate_path=ROOT/'validation/model_final_20261103/candidate.json';candidate=json.loads(candidate_path.read_text(encoding='utf-8'))
    assert candidate['calibration']=='none','Runtime calibration must be implemented before exporting a calibrated model'
    W=np.zeros((3,3,2));W[:,[0,2],:]=np.array(candidate['csp_weights'])
    w=np.array(candidate['svm_w']);b=candidate['svm_b'];scale=min(1.,30000/(np.max(np.abs(w))*2048))
    live_values={'ff_sos.txt':np.array(candidate['sos_q14']).ravel(),
                 'ff_csp.txt':np.rint(W*16384).astype(np.int64).ravel(),
                 'ff_svmw.txt':np.rint(w*scale*2048).astype(np.int64),
                 'ff_svmb.txt':[round(b*scale*(1<<23))]}
    for name,vals in live_values.items():(live/name).write_text(''.join(f'{v}\n' for v in vals),encoding='ascii')
    lm=model(live);assert np.max(np.abs(lm[1]))<=32767 and np.max(np.abs(lm[2]))<=32767 and -(1<<31)<=lm[3]<(1<<31)
    (live/'model.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        'svm_common_positive_scale':scale,'live_channels':[3,4],'pl_slots':['C3','ZERO','C4'],
        'requires_PS_notch50_Q30':True,'validated':False,'status':'exploratory; no new independent acceptance'},indent=2),encoding='utf-8')
    data_path=ROOT/'matlab/data/subj04.mat';data=loadmat(data_path,squeeze_me=True)
    X,y=data['Xte'].transpose(0,2,1).astype(np.int32),data['yte'].astype(int)
    pred=[];replay=OUT/'dataset';replay.mkdir()
    with (replay/'index.csv').open('w',newline='') as f:
        index=csv.writer(f);index.writerow(['trial','truth','file'])
        for i,(trial,truth) in enumerate(zip(X,y)):
            d=decision(projections(trial,m),m);pred.append(d)
            if i<20:
                name=f'trial_{i+1:03d}.bin'
                (replay/name).write_bytes(b''.join(packet(v,j) for j,v in enumerate(trial)))
                index.writerow([i+1,int(truth),name])
            if i%40==0:print('DATASET_REFERENCE',i+1,flush=True)
    provenance={'source':str(data_path.relative_to(ROOT)),'source_sha256':hashlib.sha256(data_path.read_bytes()).hexdigest(),
        'source_sessions':['B0404E','B0405E'],'labels':'yte in local MAT, 0 left / 1 right',
        'label_source_status':'Local MAT metadata says evaluation sessions; upstream GDF/official-label alignment must be cross-checked before claiming final accuracy.',
        'selection':'First20 trials in file order; no prediction-based selection','samples_per_trial':int(X.shape[1]),
        'trial_reset':True,'scoring':'One actual PL final-window prediction per trial, no majority vote tuning',
        'all320_integer_reference':metrics(y,np.array(pred)),
        'packaged20_integer_reference':metrics(y[:20],np.array(pred[:20])),
        'reference_checked_against_existing_RTL_vectors':119,'hardware_final_package_validated':False}
    (replay/'provenance.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')
    with (OUT/'dataset_reference_predictions.csv').open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['trial','truth','decision','prediction'])
        writer.writerows((i+1,int(t),int(d),int(d>=0)) for i,(t,d) in enumerate(zip(y,pred)))
    print(json.dumps(provenance,indent=2),flush=True)

if __name__=='__main__':main()
