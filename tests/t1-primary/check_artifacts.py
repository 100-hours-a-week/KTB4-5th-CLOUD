"""공유 결과의 자격 증명 유출 여부와 원시 자료 보존 상태를 검사한다."""
import gzip
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'results/2026-11-11'


def main():
    passwords={a['password'].encode() for a in json.loads((ROOT/'private/accounts.json').read_text(encoding='utf-8'))}
    pattern=re.compile(rb'(?:AKIA|ASIA)[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
    result={'files_scanned':0,'findings':[],'known_truncated_archives':[]}
    for path in OUT.rglob('*'):
        if not path.is_file() or path.name in ['artifact-safety.json','sha256.json']:continue
        compressed=path.name.endswith(('.gz','.gz.part'))
        stream=gzip.open(path,'rb') if compressed else path.open('rb')
        tail=b''
        try:
            with stream:
                while True:
                    chunk=stream.read(1024*1024)
                    if not chunk:break
                    data=tail+chunk
                    if any(p in data for p in passwords) or pattern.search(data):
                        result['findings'].append(str(path.relative_to(OUT)));break
                    tail=data[-4096:]
        except EOFError:
            if path.name!='truncated.json.gz.part':raise
            result['known_truncated_archives'].append(str(path.relative_to(OUT)))
        result['files_scanned']+=1
    result['pass']=not result['findings']
    (OUT/'artifact-safety.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['pass'] else 1)


if __name__=='__main__':main()
