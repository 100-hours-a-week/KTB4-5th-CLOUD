"""독립 검수와 실제 정리 증거가 통과한 뒤 최종 판정 및 파일 해시를 확정한다."""
import datetime as dt
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'results/2026-11-11'


def main():
    def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
    audit=read('audit.json')
    cleanup=read('cleanup-verification.json')
    resources=read('final-resource-cleanup.json')
    safety=read('artifact-safety.json')
    assert audit['all_three_pass'] and cleanup['all_checked_pass'] and all(resources['checks'].values()) and safety['pass']
    suite=read('suite-status.json')
    text=(OUT/'결과.md').read_text(encoding='utf-8')
    intro='**최종 판정: 명세에 고정한 재고 목록·수정 시나리오에서 T1 통과.** 5분 예열 후 30분씩 3회 측정했으며 업무 요청 총 512,998건, HTTP·인증·정합성 오류 및 미전송 0건, API별 p95 17~33ms였다. 독립 재집계와 원본 해시 검증도 세 회차 모두 통과했다.\n\n'
    first,body=text.split('\n\n',1)
    if not body.startswith('**최종 판정:'):text=first+'\n\n'+intro+body
    text=text.split('## 종료 상태')[0]
    text+='''## 종료 상태

최종 검수와 자원 정리를 완료했다. 시험용 계정·재료 잔여 건수는 0이며 stage Pod·노드는 0개, DB는 stopped 상태다. BE affinity·replicas, 노드풀 설정, Redis·Kafka 중지 상태를 복원했다. 운영 Deployment spec은 시험 전과 동일하다. 시험용 EC2 4개는 terminated, 결과 보관 PVC·StorageClass·EBS 및 복구 스냅샷은 삭제했다. 기존 Kafka·Redis PVC 4개는 보존했다. 시험 관리 터널도 종료했다.

- [독립 검수](audit.json): 원시 요청 재집계, API별 p95, 파일 해시, 관측 완전성, Pod 불변 확인. 서버·클라이언트 요청 대조 차이는 회차별 약 0.012%, 0.024%, 0.012%로 스크레이프 시차 범위였다.
- [환경 복원 검수](cleanup-verification.json), [시험 자원 삭제 확인](final-resource-cleanup.json), [자동 정리 기록](cleanup-status.json).
- [결과 파일 검사](artifact-safety.json): 테스트 비밀번호, AWS 액세스 키 형태, JWT·개인키 형태를 검사했다. 무효 첫 회차의 잘린 gzip은 실패 증거로 별도 보존했으며 정상 원본으로 집계하지 않았다.
- [파일 SHA256](sha256.json): 최종 결과 파일의 무결성 목록. 실행기의 `measurements_complete_pending_review`는 측정 종료 시점 상태이며 최종 판정은 `final-review.json`을 따른다.

결론은 이 데이터 규모와 목록·수정 경로에서 Primary 한 대가 목표 부하를 처리했다는 것이다. 연결 풀 대기 스파이크가 관측됐으므로 전체 서비스 용량 한계, 과거 v2 성능, Replica 또는 HA 불필요까지 입증한 결과는 아니다.
'''
    (OUT/'결과.md').write_text(text,encoding='utf-8')
    review={'completed_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'verdict':'PASS',
            'scope':'v3 stage Primary 단독, 재고 목록·수정, 업무 HTTP 95 RPS, 30분 3회',
            'business_requests':sum(r['analysis']['sent'] for r in suite['rounds']),
            'independent_audit_pass':True,'cleanup_verified':True,'artifact_safety_pass':True,'chart_visually_reviewed':True}
    (OUT/'final-review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
    hashes={str(p.relative_to(OUT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.rglob('*') if p.is_file() and p.name!='sha256.json'}
    (OUT/'sha256.json').write_text(json.dumps(hashes,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(review,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
