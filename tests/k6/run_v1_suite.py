"""설계의 다섯 구간을 순서대로 실행하고 민감정보 없이 진행 상태를 저장한다."""
import datetime
import json
from pathlib import Path
import subprocess
import time
import signal
from collections import deque

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / 'private'
STATE = PRIVATE / 'v1-suite-status.json'


def save(state):
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(STATE)


def main():
    state = {'startedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'status': 'running', 'runs': []}
    save(state)
    for mode in ['morning', 'peak', 'weeknight', 'weekend_lunch', 'weekend_dinner']:
        state['current'] = mode
        save(state)
        samples = PRIVATE / f'v1-{mode}-samples.json'
        samples.write_text('')
        window = deque(maxlen=100)
        consecutive = 0
        stopped = False
        with (PRIVATE / f'v1-{mode}.log').open('w', encoding='utf-8') as output:
            process = subprocess.Popen(['k6', 'run', '--quiet', '--out', f'json={samples}', '-e', f'MODE={mode}', 'v1-flow.js'],
                                    cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
            with samples.open() as stream:
                while process.poll() is None:
                    position = stream.tell()
                    line = stream.readline()
                    if not line.endswith('\n'):
                        stream.seek(position)
                        time.sleep(0.2)
                        continue
                    event = json.loads(line)
                    if event.get('type') != 'Point' or event.get('metric') != 'all_requests_failed':
                        continue
                    failed = event['data']['value'] > 0
                    window.append(failed)
                    consecutive = consecutive + 1 if failed else 0
                    # 단발성 오류는 허용하고 연속 또는 누적 오류 증가만 중단한다.
                    if consecutive >= 5 or (len(window) >= 20 and sum(window) >= 5 and sum(window) / len(window) >= 0.05):
                        stopped = True
                        state['stopReason'] = '연속 5회 실패 또는 최근 최대 100요청에서 20건 이상 관측 후 5건 이상 및 5% 이상 실패'
                        save(state)
                        process.send_signal(signal.SIGINT)
                        break
            try:
                exit_code = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                exit_code = process.wait()
        summary = PRIVATE / f'v1-{mode}.json'
        metrics = json.loads(summary.read_text())['metrics'] if summary.exists() else {}
        # 응답시간 목표만 미달했다면 다음 예정 구간을 실행한다.
        completed = metrics.get('journeys_completed', {}).get('values', {}).get('count', 0)
        expected = {'morning': 140, 'peak': 700, 'weeknight': 100,
                    'weekend_lunch': 150, 'weekend_dinner': 150}[mode]
        attempts = metrics.get('journeys_attempted', {}).get('values', {}).get('count', 0)
        state['runs'].append({'mode': mode, 'exitCode': exit_code,
                              'attempted': attempts,
                              'completed': completed, 'expected': expected})
        if stopped or attempts != expected or exit_code not in (0, 99):
            state['status'] = 'stopped_on_failure'
            break
        save(state)
    else:
        state['status'] = 'completed'
    state['finishedAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save(state)


if __name__ == '__main__':
    main()


