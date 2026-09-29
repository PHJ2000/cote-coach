"""Measure cold vs prepared completion using isolated, real JDT workspaces."""
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from coach.ide import JavaIDE

root=Path(__file__).resolve().parents[1]
code='class Solution { void run(String text){text.sub} }'
position=code.index('text.sub')+len('text.sub')
measurements={}
for prepared in (False,True):
    with tempfile.TemporaryDirectory(prefix='cote-startup-') as folder:
        ide=JavaIDE(root,Path(folder))
        try:
            if prepared:
                start=time.perf_counter();ide.warmup()
                measurements['app_startup_ms']=round((time.perf_counter()-start)*1000)
                start=time.perf_counter();ide.prepare('practice','class Solution {}')
                measurements['session_preparation_ms']=round((time.perf_counter()-start)*1000)
            start=time.perf_counter()
            result=ide.request('practice',code,'completion',0,position)
            measurements['prepared_first_completion_ms' if prepared else 'cold_first_completion_ms']=round((time.perf_counter()-start)*1000)
            assert any('substring' in item['label'] for item in result['items'])
        finally:
            if ide.rpc:ide.rpc.close()
print(measurements,flush=True)
assert measurements['prepared_first_completion_ms']<measurements['cold_first_completion_ms'],measurements
print('PASS real Java cold-start preparation (timings depend on the machine).')
