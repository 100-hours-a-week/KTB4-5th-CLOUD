# CLOUD 문서

| 폴더 | 문서 | 내용 |
|---|---|---|
| `design/wiki/` | [ktb4-cloud-wiki-v1.md](design/wiki/ktb4-cloud-wiki-v1.md) | 1단계: EC2 + Docker Compose 초기 배포 |
| | [ktb4-cloud-wiki-ci.md](design/wiki/ktb4-cloud-wiki-ci.md) | 2단계: CI |
| | [ktb4-cloud-wiki-cd.md](design/wiki/ktb4-cloud-wiki-cd.md) | 3단계: CD |
| | [ktb4-cloud-wiki-v2.md](design/wiki/ktb4-cloud-wiki-v2.md) | 4단계: 트래픽 급증 대비 확장·Auto Scaling |
| `design/` | [v1 Compose 운영 한계 분석](design/v1%20Compose%20한계%20분석%20및%20ECS%20전환%20검토.md) | 현재 컨테이너 관리의 불편함과 서버·서비스 확장 시 운영 부담 |
| | [v2 인프라 설계 전 배경.md](design/v2%20인프라%20설계%20전%20배경.md) | v2 인프라 설계 |
| | [v2 구현 계획.md](design/v2%20구현%20계획.md) | v2 날짜별 구현 계획(2026-10-06 ~ 10-16) |
| | [v2 환경별 구축 아키텍처](design/v2%20환경별%20구축%20아키텍처.md) | 실제 dev·stage·prod 구성도, 배치·가동 상태와 목표 설계의 차이 |
| | [인프라 설계 의사결정 근거.md](design/인프라%20설계%20의사결정%20근거.md) | 결정별 상세 이유·근거·트레이드오프 |
| | [v3 인프라 설계 전 배경.md](design/v3%20인프라%20설계%20전%20배경.md) | v3 설계 배경 |
| | [v3 인프라 설계.md](design/v3%20인프라%20설계.md) | v3 인프라 설계 |
| `implementation/` | [v2 구현 기록.md](implementation/v2%20구현%20기록.md) | v2 구현 중 정한 것(I1~I6), 배포 파이프라인과 dev 구축 순서(ECS 인스턴스 1대, 단계별 PR) |
| | [v2 network 스택 작성 가이드.md](implementation/v2%20network%20스택%20작성%20가이드.md) | network.yaml 단계별 작성 예시 |
| | [v2 prod 전환 방식.md](implementation/v2%20prod%20전환%20방식.md) | v1 prod → v2 prod 전환 방법 조사 |
| | [v2 데이터베이스 무중단 마이그레이션 검토.md](implementation/v2%20데이터베이스%20무중단%20마이그레이션%20검토.md) | DB 이전 방식 후보 검토. 이전 절차는 [위키](https://github.com/100-hours-a-week/KTB4-5th-wiki/wiki/Cloud-4단계-DB-무중단-마이그레이션-설계) |
| | [v2 데이터베이스 마이그레이션 테스트 결과.md](implementation/v2%20데이터베이스%20마이그레이션%20테스트%20결과.md) | DB 이전 리허설 결과 |
| | [v2 prod 전환 결과.md](implementation/v2%20prod%20전환%20결과.md) | 10/19 운영 전환·DB 이전 결과와 장애 분석 |
| | [10/19 DB 마이그레이션 쓰기 실패 트러블슈팅](implementation/2026-10-19_DB_마이그레이션_중_쓰기_실패_트러블슈팅.md) | BE 연결 전환 지연의 원인과 재발 방지 방향 |
| | [10/26 Batch 중복 실행 분석](implementation/2026-10-26%20Batch%20중복%20실행%20분석.md) | 정기 Batch 중복 실행 원인 분석 |
| | [v3 비용 산정 및 구현 예산.md](implementation/v3%20비용%20산정%20및%20구현%20예산.md) | v3 구성 비용 산정 |
| | [v3 dev 환경 구축 계획.md](implementation/v3%20dev%20환경%20구축%20계획.md) | v3 dev 구축 순서와 사전 조회 |
| | [v3 dev 환경 구축 기록.md](implementation/v3%20dev%20환경%20구축%20기록.md) | v3 dev 구축 진행 기록 |
| `observability/` | [SLI-SLO-design.md](observability/SLI-SLO-design.md) | SLI·SLO 설계 |
| | [monitoring-alerting-design.md](observability/monitoring-alerting-design.md) | CloudWatch 모니터링·알림 설계(구현: `infra/monitoring`) |
| | [cloudwatch-agent-setup.md](observability/cloudwatch-agent-setup.md) | CloudWatch 로그 수집(nginx·BE·Agent) 적용 기록 |
| `assets/` | 이미지 | 문서에서 참조하는 그림 |

`design/wiki/` 문서는 GitHub Wiki에 옮기는 원본입니다. 구현 중 장애 분석은 `implementation/`에 두고, 그 밖의 트러블슈팅 기록은 저장소 밖 `기술 문서` 폴더에서 관리합니다.
