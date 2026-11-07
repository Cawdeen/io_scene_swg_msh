"""Read-only corpus check: python tests/check_animation_corpus.py ANIMATION_DIR."""
from collections import Counter
import importlib.util
from pathlib import Path
import sys

spec=importlib.util.spec_from_file_location('ans_format',Path(__file__).parents[1]/'io_scene_swg/ans_format.py')
ans=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=ans
spec.loader.exec_module(ans)
counts=Counter()
failures=[]
warnings=[]
files=sorted(Path(sys.argv[1]).glob('*.ans'))
if not files:raise SystemExit('No .ans files found')
for index,path in enumerate(files,1):
    try:
        clip=ans.load(path)
        counts[clip.source_format]+=1
        if clip.warnings:warnings.append(path.name)
    except (ValueError,OSError) as error:
        failures.append((path.name,str(error)))
    if index%1000==0:print('CORPUS checked',index,'of',len(files),flush=True)
print('CORPUS formats',dict(counts),'failures',len(failures),'rounding warnings',len(warnings),flush=True)
for name,error in failures[:20]:print('FAIL',name,error)
for name in warnings:print('ROUNDING',name)
raise SystemExit(bool(failures))
