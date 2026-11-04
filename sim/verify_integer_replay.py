"""Recompute frozen20 using the original integer reference, not supplied labels."""
from pathlib import Path
import sys,csv,json
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'training'));sys.path.insert(0,str(ROOT/'sim/reference'))
import numpy as np
import gen_log2_lut as logs
from prepare_final_assets_20261103 import model,projections,decision
logs.LUT=np.array([int(s,16) for s in (ROOT/'src/pl/log2_lut.hex').read_text().split()])
m=model(ROOT/'data/models/dataset');rows=list(csv.DictReader((ROOT/'data/dataset/index.csv').open()));results=[]
for row in rows:
    raw=(ROOT/'data/dataset'/row['file']).read_bytes()
    x=np.array([[int.from_bytes(raw[i+2+c*3:i+5+c*3],'big',signed=True) for c in range(3)] for i in range(0,len(raw),33)],dtype=np.int64)
    d=decision(projections(x,m),m);word=((d&0xfffffffe)|int(d>=0))&0xffffffff
    assert word==int(row['expected_final_word'],16),(row['trial'],hex(word),row['expected_final_word'])
    results.append({'trial':int(row['trial']),'truth':int(row['truth']),'prediction':int(d>=0),'decision_word':f'{word:08x}'})
print(json.dumps({'scope':'Offline integer reference only; not new board inference','trials':len(results),'correct':sum(r['truth']==r['prediction'] for r in results),'exact_reference_words':len(results),'predictions':results},indent=2))
