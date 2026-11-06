# 준비 변경 검증 기록

검증일: 2026-11-06 KST. 기준 main: `e8aa891e40c825fe13a65b4f1f97c51b65f00a58`.
브랜치: `codex/stage-load-test-preparation`. 기존 CLOUD 기본 checkout·FE·BE 파일은 수정하지 않았습니다.

## 통과

- Python 경계·수집기 테스트 10개. 기본 실행의 네트워크/부하 없음, dev 대상 차단, 과도한 부하 차단, NTP 실패·로컬 CMOS 거부, 잘못된 kube context 거부, smoke 선행, Pod 환경변수 제외, 이벤트 시각 보존, 모의 수집기의 초기 동시 조회·정상 종료를 확인했습니다.
- Helm 렌더링: Alloy DaemonSet, 이벤트 수집 Deployment, kube-state-metrics, application(dev/stage BE), platform. stage toleration, 단일 이벤트 수집기, 이벤트 전용 권한, HPA collector/RBAC, stage Actuator 설정, 시험 HPA 한정 종료 권한을 확인했습니다.
- Alloy v1.21.3 validate: Node 수집 및 이벤트 수집 설정 2개.
- PromQL parser 0.12.0: 새 대시보드 쿼리 24개 구문 검사.
- k6 v2.3.0 inspect: smoke/HPA 실행 계획·옵션을 가짜 임시 계정으로 검사. dev URL은 거부되었습니다. **k6 run은 실행하지 않았습니다.**
- cfn-lint 1.60.0: 변경한 observability 템플릿. 파일 43,176 bytes로 inline template 크기 제한 안에 있습니다.
- Git Bash `bash -n`: stage power 스크립트. 본문은 실행하지 않았습니다.
- CI YAML 구조·배포 인증 없음 확인, 대시보드 생성 일치, `git diff --check`.

차트는 Alloy 1.15.0 / kube-state-metrics 9.0.0을 사용했습니다. Python 3.13·Windows에서 검증했으며 Ubuntu CI 자체는 아직 실행하지 않았습니다. 검증 중 발견한 이벤트 차트의 빈 RBAC 목록 렌더 오류와 cfn-lint 진입점 오류는 수정한 뒤 검사를 통과했습니다. Windows 샌드박스의 임시 폴더·Git Bash 실행 제한은 로컬 검증 명령만 승인 범위에서 다시 실행하여 확인했습니다.

## 아직 검증하지 않은 항목

- 새 설정의 클러스터·모니터링 EC2 적용, stage 기동 및 신규 Node의 Alloy 배치/실제 타깃 up.
- stage Actuator endpoint·histogram·로그·HPA 상태·ALB target health의 실제 동작.
- 실제 stage 전용 계정 인증 및 시험 데이터 분포, 부하 발생 호스트 NTP.
- 실제 HPA 확장·Node 증설 속도와 CPU 부하 임계 도달 여부. 계획의 20 iterations/s는 초기 시험값이며 확장을 보장하지 않습니다.
- 새 Pod 첫 요청의 정확한 시각. 현재 증거는 15초 메트릭·5초 상태 조회의 근사 범위이며 정확한 요청 단위 증명은 access log 추가가 필요합니다.

적용 순서와 실행·중단·원복 명령은 `README.md`에 있습니다. 실제 적용 전 main과 설치별 수동 설정 차이를 확인하고, 적용 뒤 preflight → smoke → 시험 HPA → HPA 부하 순서로 검증합니다.
