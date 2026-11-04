"""Check frozen release inputs; never accesses hardware or substitutes a prediction."""
from pathlib import Path
import csv,hashlib,json
ROOT=Path(__file__).resolve().parents[1]
def main():
    checked=0
    for line in (ROOT/'manifest.sha256').read_text().splitlines():
        digest,name=line.split('  ',1);p=ROOT/name
        assert p.is_file(),name
        assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,name
        checked+=1
    for mode in ('live','dataset'):
        for file,count in [('ff_sos.txt',30),('ff_csp.txt',18),('ff_svmw.txt',4),('ff_svmb.txt',1)]:
            assert len((ROOT/'data/models'/mode/file).read_text().split())==count,(mode,file)
    rows=list(csv.DictReader((ROOT/'data/dataset/index.csv').open()))
    assert len(rows)==20
    assert sum(int(r['truth']) for r in rows)==10
    for r in rows:
        raw=(ROOT/'data/dataset'/r['file']).read_bytes();assert len(raw)==876*33
        for i in range(876):
            packet=raw[i*33:(i+1)*33]
            assert packet[0] in (0xa0,0x41)
            assert (packet[-1]&0xf0)==0xc0
            if i:assert packet[1]==(raw[(i-1)*33+1]+1)&255
        assert int(r['expected_final_word'],16)&1==int(r['truth'])
    for p in (ROOT/'training').glob('*.py'):compile(p.read_text(encoding='utf-8'),str(p),'exec')
    binaries=json.loads((ROOT/'board/prebuilt/metadata.json').read_text())
    assert len(binaries)==4 and all(r['elf_class']==32 and r['elf_machine']=='ARM' and r['archived_privately'] for r in binaries.values())
    print(json.dumps({'manifest_files':checked,'parameter_words_per_mode':53,'replay_trials':20,'samples_per_trial':876,'privately_archived_ARM_programs':4,'scope':'Static input, provenance and packet checks only; no new RTL or board inference executed.'},indent=2))
if __name__=='__main__':main()
