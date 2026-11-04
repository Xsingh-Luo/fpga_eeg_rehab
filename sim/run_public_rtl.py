"""Reproduce two public-data UART/AXIS RTL cases in a new XSim directory."""
from pathlib import Path
import argparse,json,shutil,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'training'),str(ROOT/'sim/reference')]
import numpy as np
import gen_log2_lut as logs
from prepare_final_assets_20261103 import model,projections,decision

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--vivado-bin',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 out=a.out.resolve()
 if out.exists():p.error('Refuse to overwrite output directory')
 out.mkdir(parents=True)
 for n in ('tb_axis_fullflow.v','axis_fullflow_checks.vh'):shutil.copy2(ROOT/'sim/rtl'/n,out/n)
 shutil.copy2(ROOT/'src/pl/log2_lut.hex',out/'log2_lut.hex')
 def tool(name,args):
  r=subprocess.run([str((a.vivado_bin/(name+'.bat')).resolve())]+args,cwd=out,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=240)
  (out/(name+'_console.txt')).write_text(r.stdout+r.stderr,encoding='utf-8')
  if r.returncode:raise RuntimeError(name+' failed; inspect console file')
  return r.stdout
 names=('uart_rx.v','pkt_parser.v','dc_block.v','biquad_bank.v','csp_project.v','win_var.v','lognorm.v','svm_core.v','axil_regs.v','clk_pll.v','eeg_csp_svm_top.v','axis_sample_input.v')
 tokens=['--sv','-d','SIMULATION','-d','N_PKT=876','-d','N_BYTE=28908',str(out/'tb_axis_fullflow.v')]+[str(ROOT/'src/pl/rtl'/n) for n in names]
 (out/'xvlog.options').write_text('\n'.join(s.replace('\\','/') for s in tokens)+'\n',encoding='utf-8')
 tool('xvlog',['-f',str(out/'xvlog.options')]);tool('xelab',['work.tb_axis_fullflow','-s','public_replay'])
 logs.LUT=np.array([int(s,16) for s in (ROOT/'src/pl/log2_lut.hex').read_text().split()])
 bank=ROOT/'data/models/dataset';m=model(bank);results=[]
 for trial in (1,2):
  raw=(ROOT/f'data/dataset/trial_{trial:03}.bin').read_bytes();assert len(raw)==876*33
  framed=bytearray(raw)
  for i in range(0,len(framed),33):framed[i]=0x41
  for n in ('ff_sos.txt','ff_csp.txt','ff_svmw.txt','ff_svmb.txt'):shutil.copy2(bank/n,out/n)
  (out/'ff_bytes.txt').write_text(''.join(f'{b}\n' for b in framed),encoding='ascii')
  text=tool('xsim',['public_replay','-runall']);assert 'AXIS_FULLFLOW_PASS' in text
  actual=np.loadtxt(out/'axis_actual_dec.txt',dtype=np.int64)
  x=np.array([[int.from_bytes(raw[i+2+c*3:i+5+c*3],'big',signed=True) for c in range(3)] for i in range(0,len(raw),33)])
  z=projections(x,m);expected=np.array([decision(z[:i+1],m) for i in range(625,876)])
  assert np.array_equal(actual[625:,1],expected),'RTL/reference mismatch'
  dst=out/f'trial_{trial:03}';dst.mkdir()
  for f in out.glob('*.txt'):shutil.copy2(f,dst/f.name)
  results.append({'trial':trial,'post_warmup_exact_decisions':len(expected),'final_decision':int(expected[-1]),'label':int(expected[-1]>=0)})
  print('PUBLIC_RTL_CASE_PASS',trial,flush=True)
 (out/'verification.json').write_text(json.dumps({'passed':True,'scope':'UART/AXIS RTL simulation only; not physical board inference','UART_header_only_normalized_A0_to_41':True,'cases':results},indent=2),encoding='utf-8')
if __name__=='__main__':main()
