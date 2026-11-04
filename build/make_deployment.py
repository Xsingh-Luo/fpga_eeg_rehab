from pathlib import Path
import argparse,shutil,hashlib
p=argparse.ArgumentParser();p.add_argument('--image',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
b=Path(__file__).resolve().parents[1]
if a.out.exists():raise SystemExit('Refuse overwrite')
if not a.image.is_file():raise SystemExit('Provide the matching privately archived image.ub')
shutil.copytree(b/'board/deployment',a.out)
shutil.copytree(b/'src/ps_app',a.out/'ps_app')
for group in ('models','dataset'):shutil.copytree(b/'data'/group,a.out/group)
for n in ('bci_app','bci_run','st7789_display','pl_sample_replay'):
 source=b/'src/ps_app'/n
 if not source.is_file():raise SystemExit('Build ARM programs first: bash build/build_arm.sh')
 shutil.copy2(source,a.out/n)
shutil.copy2(a.image,a.out/'image.ub')
files=sorted(f for f in a.out.rglob('*') if f.is_file())
(a.out/'source.sha256').write_text(''.join(f'{hashlib.sha256(f.read_bytes()).hexdigest()}  ./{f.relative_to(a.out).as_posix()}\n' for f in files),encoding='ascii')
print(a.out)
