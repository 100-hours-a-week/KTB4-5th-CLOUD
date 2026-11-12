# GitHub 업로드 선별

이 문서와 `.gitignore`에 따라 T1 코드 및 `github/` 게시본을 올린다. 전체 실행 결과는 로컬에 보존한다. PR에는 아래 선별 파일만 포함한다.

| 구분 | 게시 대상 | 목적 |
|---|---|---|
| 명세 | `README.md`, `GITHUB_UPLOAD.md`, `.gitignore` | 범위·판정 기준·게시 정책 |
| 부하 및 준비 | `load.js`, `prepare.js`, `supervise.sh` | 95 HTTP RPS 시나리오와 데이터 준비 |
| 실행 및 관측 | `run.py`, `suite.py`, `remote.py`, `db-observe.sh` | 실행·관측·원본 전송 검증 |
| 정리 및 복원 | `cleanup.py`, `cleanup_when_done.py`, `verify_cleanup.py` | 테스트 데이터 정리와 환경 복원 확인 |
| 분석 및 검수 | `analyze.py`, `test_analyze.py`, `audit.py`, `report.py`, `check_artifacts.py`, `finalize_report.py` | 집계·독립 검수·보고서 생성 |
| 최종 결과 | `github/results/결과.md`, `resource-summary.png`, `resource-summary.json` | 결론·그래프·자원 및 크레딧 요약 |
| 근거 | `github/results/round-*/analysis.json`, `round-*-credits-*.json` | 회차별 집계와 DB 크레딧 원본 |
| 검수 기록 | `github/results/`의 나머지 JSON | 독립 검수·정리 확인·파일 검사·전송 방식 검증 |
| 파일 목록 | `github/UPLOAD_MANIFEST.json` | 정확한 게시 파일 목록·용량·SHA256 |

## 제외 대상

- `private/`: 계정 비밀번호, 인증 세션, 운영 환경 조회 자료와 실행 중간 파일.
- `results/`: 전체 원본 묶음. 요청별 gzip, 관측 JSONL, DB 원본, 실패·중단 회차 자료, 상세 환경 스냅샷과 전체 해시를 포함한다.
- `__pycache__/`: Python 실행 캐시.

실패·중단 회차의 경위와 제약은 게시 보고서에도 남겼다. 원본을 제외한 이유는 용량과 인증·내부 환경 정보 관리이며 실패 기록을 성공으로 집계하지 않았다. 제외 파일은 삭제하지 않았다.

## 실행 및 검토 범위

실행 도구는 이번 stage 시험의 환경별 AWS profile·리소스 식별자·경로를 사용한다. 새 환경에서는 대상과 설정을 검토해야 한다. `private/`의 계정·fixture·복원 기준 스냅샷과 runner 정의는 게시하지 않으므로 이 저장소만 복제해 전체 환경을 즉시 구축하는 패키지는 아니다. 부하 모델·판정·분석·복원 절차를 검토하고 해당 환경에 맞게 재사용하기 위한 코드다.

게시본에는 비밀번호·인증 쿠키·JWT·개인키가 없는지 검사했다. 결과의 원시 재분석은 별도 보관된 전체 자료가 필요하며, 게시된 독립 검수 기록은 그 전체 자료를 대상으로 한 결과다.
