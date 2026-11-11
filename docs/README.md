# CLOUD 문서

설계 문서는 [팀 위키](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki)에서 관리합니다. 페이지 이름은 `Cloud-<단계>단계-<주제>`이고, 설계 문서의 이전 판은 위키의 페이지 이력에서 볼 수 있습니다. 이 폴더에는 구현 기록과 관측 문서를 둡니다.

## 설계 문서(위키)

| 단계 | 문서 | 내용 |
|---|---|---|
| 1단계(v1) | [초기 배포 설계](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-1단계-초기-배포-설계) | EC2 + Docker Compose 초기 배포 |
| 2단계(v1) | [CI 설계](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-2단계-CI-설계) | CI 파이프라인 |
| 3단계(v1) | [CD 설계](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-3단계-CD-설계) | CD 파이프라인 |
| 4단계(v2) | [v1 운영 한계 분석](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-4단계-v1-운영-한계-분석) | Compose 운영 한계와 ECS 전환 검토 |
| | [인프라 설계 전 배경](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-4단계-인프라-설계-전-배경) | 트래픽 가정과 고려 사항 |
| | [인프라 확장 설계](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-4단계-인프라-확장-설계) | ECS 기반 확장·Auto Scaling 설계 |
| | [인프라 설계 의사결정 근거](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-4단계-인프라-설계-의사결정-근거) | 결정별 이유·근거·트레이드오프 |
| | [구축 결과 아키텍처](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-4단계-구축-결과-아키텍처) | dev·stage·prod 환경별 구성 |
| | [DB 무중단 마이그레이션 설계](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-4단계-DB-무중단-마이그레이션-설계) | DB 이전 절차 |
| 5단계(v3) | [인프라 설계 전 배경](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-5단계-인프라-설계-전-배경) | Kubernetes 도입 배경 |
| | [인프라 설계](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-5단계-인프라-설계) | AWS EKS 운영 플랫폼 |
| | [인프라 설계 의사결정 근거](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-5단계-인프라-설계-의사결정-근거) | 결정별 후보 비교·선택 근거 |
| | [인프라 고도화 계획](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-5단계-인프라-고도화-계획) | 구축 이후 검토할 변경 |
| | [GCP 재해복구 설계 초안](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-5단계-GCP-재해복구-설계-초안) | AWS 장애 시 GCP 복구 |
| 공통 | [SLI·SLO 설계](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-SLI-SLO-설계) | 사용자 여정별 SLI·SLO |

## 이 저장소의 문서

| 폴더 | 문서 | 내용 |
|---|---|---|
| `implementation/` | [v2 구현 계획.md](implementation/v2%20구현%20계획.md) | v2 날짜별 구현 계획(2026-10-06 ~ 10-16)과 실제 진행 |
| | [v2 구현 기록.md](implementation/v2%20구현%20기록.md) | v2 구현 중 정한 것(I1~I6), 배포 파이프라인과 dev 구축 순서 |
| | [v2 데이터베이스 무중단 마이그레이션 검토.md](implementation/v2%20데이터베이스%20무중단%20마이그레이션%20검토.md) | DB 이전 방식 후보 검토 |
| | [v2 데이터베이스 마이그레이션 테스트 결과.md](implementation/v2%20데이터베이스%20마이그레이션%20테스트%20결과.md) | DB 이전 리허설 결과 |
| | [v2 prod 전환 결과.md](implementation/v2%20prod%20전환%20결과.md) | 10/19 운영 전환·DB 이전 결과와 장애 분석 |
| | [10/19 DB 마이그레이션 쓰기 실패 트러블슈팅](implementation/2026-10-19_DB_마이그레이션_중_쓰기_실패_트러블슈팅.md) | BE 연결 전환 지연의 원인과 재발 방지 방향 |
| | [10/26 Batch 중복 실행 분석](implementation/2026-10-26%20Batch%20중복%20실행%20분석.md) | 정기 Batch 중복 실행 원인 분석 |
| `observability/` | [monitoring-alerting-design.md](observability/monitoring-alerting-design.md) | v2 CloudWatch 모니터링·알림 설계(구현: `infra/monitoring`) |
| | [cloudwatch-agent-setup.md](observability/cloudwatch-agent-setup.md) | v2 CloudWatch 로그 수집(nginx·BE·Agent) 적용 기록 |

구현 중 장애 분석은 `implementation/`에 두고, 그 밖의 트러블슈팅 기록은 저장소 밖 `기술 문서` 폴더에서 관리합니다.
