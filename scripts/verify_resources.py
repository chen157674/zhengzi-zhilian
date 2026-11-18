"""按交付清单核对全部资源；只读，不联网。"""
from pathlib import Path
import argparse,hashlib,json,sys
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--quick',action='store_true');args=parser.parse_args()
doc=json.loads((root/'交付资源清单.json').read_text('utf-8'));bad=[]
for item in doc['files']:
 p=(root/item['path']).resolve();p.relative_to(root)
 if not p.is_file() or p.stat().st_size!=item['bytes']:bad.append(item['path']);continue
 if not args.quick:
  with p.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
  if actual!=item['sha256']:bad.append(item['path'])
if bad:
 print('资源不完整：'+'\n'.join(bad));sys.exit(1)
print('资源校验通过，共'+str(len(doc['files']))+'项。')
