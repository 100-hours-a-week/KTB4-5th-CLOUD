"""시험 완료·실패 시 자동 정리하며 대기 상한은 세 시간이다."""
import json
import subprocess
import sys
import time
from run import ROOT, kubectl

started=time.monotonic()
while True:
    status=json.loads((ROOT/'private/suite-status.json').read_text(encoding='utf-8'))
    if status.get('phase') in ['stopped','measurements_complete_pending_review']:
        break
    if time.monotonic()-started > 10800:
        try:
            kubectl('exec','-n','stage','t1-runner','--','sh','-c','kill -INT "$(cat /work/load.pid)"')
        except Exception:
            pass
        break
    time.sleep(15)
raise SystemExit(subprocess.call([sys.executable,str(ROOT/'cleanup.py')]))
