"""T1 시험의 명시적 대상에만 SSM 명령을 전송한다."""
import argparse
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def aws(*args):
    result = subprocess.run(['aws', *args, '--profile', 'dameokja-aws-cli',
                             '--region', 'ap-northeast-2', '--output', 'json'],
                            capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace'))
    try:
        output = result.stdout.decode('utf-8')
    except UnicodeDecodeError:
        output = result.stdout.decode('cp949')
    # 삭제 API 등은 성공 시 응답 본문이 없을 수 있다.
    return json.loads(output) if output.strip() else {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('instance', choices=['i-081d444e5e162a1c2', 'i-04994dbed6ed19164'])
    parser.add_argument('script', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    params = ROOT / 'private' / 'ssm-parameters.json'
    params.write_text(json.dumps({'commands': [args.script.read_text(encoding='utf-8-sig')],
                                 'executionTimeout': ['600']}), encoding='utf-8')
    command = aws('ssm', 'send-command', '--instance-ids', args.instance,
                  '--document-name', 'AWS-RunShellScript', '--parameters', f'file://{params}')
    cid = command['Command']['CommandId']
    print(f'SSM command: {cid}', flush=True)
    for _ in range(300):
        time.sleep(2)
        try:
            result = aws('ssm', 'get-command-invocation', '--command-id', cid,
                         '--instance-id', args.instance)
        except RuntimeError as error:
            if 'InvocationDoesNotExist' in str(error):
                continue
            raise
        if result['Status'] not in ['Pending', 'InProgress', 'Delayed']:
            if args.output:
                args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(result['Status'])
            print(result['StandardOutputContent'])
            print(result['StandardErrorContent'])
            raise SystemExit(0 if result['Status'] == 'Success' else 1)
    raise TimeoutError(cid)


if __name__ == '__main__':
    main()
