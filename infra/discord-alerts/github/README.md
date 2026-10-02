# GitHub Discord 알림

이슈 멘션과 CI/CD 결과 알림을 기능별로 관리한다. [전체 알림 구성도](../README.md)를 함께 참고한다.

| 기능 | 관리 폴더 | 실행 위치 |
|---|---|---|
| 이슈·댓글의 새 개인 멘션 | [mentions](mentions/README.md) | 각 저장소 `.github/workflows/discord-mentions.yml` |
| CI 실패·배포 실패·롤백 결과·선택적 성공 | [deployment](deployment/README.md) | 각 저장소 `.github/workflows/discord-cicd.yml` |

`mentions/discord-mentions.yml`은 공통 관리본이다. `deployment/notify.py`는 공통 소스이고 `deployment/workflows/`의 파트별 YAML은 생성된 배포용 파일이다. `.github/workflows/`는 CLOUD 저장소에서 실제 실행하는 사본이므로 중복처럼 보여도 삭제하지 않는다.

웹훅과 사용자 매핑은 각 저장소의 Actions Secrets에 보관한다. 수정·설치 절차와 기본 브랜치 조건은 각 기능의 README를 따른다.
