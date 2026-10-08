# CLOUD 문서

| 폴더 | 문서 | 내용 |
|---|---|---|
| `wiki/` | [ktb4-cloud-wiki-v1.md](wiki/ktb4-cloud-wiki-v1.md) | 1단계: EC2 + Docker Compose 초기 배포 |
| | [ktb4-cloud-wiki-ci.md](wiki/ktb4-cloud-wiki-ci.md) | 2단계: CI |
| | [ktb4-cloud-wiki-cd.md](wiki/ktb4-cloud-wiki-cd.md) | 3단계: CD |
| | [ktb4-cloud-wiki-v2.md](wiki/ktb4-cloud-wiki-v2.md) | 4단계: 트래픽 급증 대비 확장·Auto Scaling |
| `design/` | [v2 인프라 설계 전 배경.md](design/v2%20인프라%20설계%20전%20배경.md) | v2 인프라 설계 |
| | [v3 인프라 설계 전 배경.md](design/v3%20인프라%20설계%20전%20배경.md) | v3 설계 배경 |
| | [v3 인프라 설계.md](design/v3%20인프라%20설계.md) | v3 인프라 설계 |
| | [인프라 설계 의사결정 근거.md](design/인프라%20설계%20의사결정%20근거.md) | 결정별 상세 이유·근거·트레이드오프 |
| `implementation/` | [v2 구현 기록.md](implementation/v2%20구현%20기록.md) | v2 구현 중 정한 것(I1~I6), 배포 파이프라인과 dev 구축 순서(ECS 인스턴스 1대, 단계별 PR) |
| `observability/` | [SLI-SLO-design.md](observability/SLI-SLO-design.md) | SLI·SLO 설계 |
| | [monitoring-alerting-design.md](observability/monitoring-alerting-design.md) | CloudWatch 모니터링·알림 설계(구현: `infra/monitoring`) |
| | [cloudwatch-agent-setup.md](observability/cloudwatch-agent-setup.md) | CloudWatch 로그 수집(nginx·BE·Agent) 적용 기록 |
| `assets/` | 이미지 | 문서에서 참조하는 그림 |

`wiki/` 문서는 GitHub Wiki에 옮기는 원본입니다. 트러블슈팅 기록은 저장소 밖 `기술 문서` 폴더에서 관리합니다.
