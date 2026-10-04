# CI/CD 결과 Discord 알림

배포용 YAML을 수정하지 않고 완료된 실행을 `workflow_run`으로 관찰한다.
각 파트 저장소에는 생성된 YAML 하나만 `.github/workflows/discord-cicd.yml`로 추가한다.
소스 코드는 CLOUD 저장소의 이 폴더에서 관리한다.

## 적용 범위

| 파트 | 현재 감시할 작업 |
|---|---|
| FE | FE v1 CI-CD의 검증·이미지 빌드 및 푸시·SSH 배포 |
| BE | BE v1 CI-CD의 테스트·JAR·이미지 빌드 및 푸시·SSH 배포 |
| CLOUD | 인프라 설정 검사 실패 |
| AI | 현재 CI/CD 없음. 추후 push/pull_request 기반 CI/CD 추가 시 자동 감시 |

모든 워크플로 이름을 감시하지만 원본 이벤트가 push/pull_request일 때만 처리한다.
이슈 멘션과 결과 알림 자체는 감시 대상에서 제외된다. 취소는 알리지 않는다.
배포 코드 변경은 없으며 Lambda·AWS 알림과는 독립적이다.

## 동작

- 빌드, 테스트, 레지스트리 로그인·이미지 푸시, 인프라 검사 실패는 파트 채널로 멘션 없이 보낸다.
- `SSH 배포` 단계가 있는 작업이 실패하면 같은 파트 담당자를 멘션한다.
- 현재 이미지 빌드·푸시는 한 단계이므로 둘 중 어느 내부 동작이 실패했는지는 실행 링크에서 확인한다.
- 기존 배포 스크립트의 정확한 결과 문구가 로그에 있을 때만 롤백 성공/실패/이전 이미지 없음으로 표시한다.
- SSH 연결 단절, 타임아웃, 로그 접근 실패 등으로 확인할 수 없으면 `확인 불가`라고 표시한다.
- 롤백 실패가 확인되면 긴급 제목으로 보낸다. 로그 원문은 Discord에 보내지 않는다.
- 배포 성공은 기본적으로 알리지 않는다. `DISCORD_NOTIFY_SUCCESS=true`일 때 성공한 SSH 배포만 멘션 없이 알린다.
- 알림은 원본 실행 완료 후 한 번 처리하므로 롤백 대기 시간을 포함한다. 재실행은 회차별로 알린다.
- 알림 워크플로 실패는 원래 배포 결과를 바꾸지 않는다. 알림 워크플로의 실패도 Actions에서 확인한다.
- 외부 저장소/PR 코드를 checkout하거나 실행하지 않으며 `actions: read`만 사용한다.

## 설정 위치

각 저장소의 Settings → Secrets and variables → Actions:

| 종류 | 이름 | 용도 |
|---|---|---|
| Secret | `DISCORD_FE_WEBHOOK_URL` / `DISCORD_BE_WEBHOOK_URL` / `DISCORD_AI_WEBHOOK_URL` / `DISCORD_CLOUD_WEBHOOK_URL` | 해당 파트의 기존 웹훅 재사용 |
| Secret | `DISCORD_USER_MAP` | 기존 GitHub 사용자 매핑 재사용. part가 해당 파트인 사용자만 멘션, 최대 50명 |
| Variable | `DISCORD_NOTIFY_SUCCESS` | 선택. 문자열 `true`이면 배포 성공 알림 활성화 |

`GITHUB_TOKEN`은 GitHub가 자동 발급한다. PAT나 추가 AWS 설정은 필요 없다.
PR 작성자·브랜치 이름은 데이터로만 처리하고 자동 멘션은 금지한다.
매핑을 비우거나 잘못 설정하면 멘션 없이 보낸다.

## 반영

`workflows/discord-cicd-fe.yml` 등 네 파일 중 대상 파트의 파일을 해당 저장소의 `.github/workflows/discord-cicd.yml`에 반영한다.
각 YAML 안에 `notify.py`와 같은 코드가 들어 있으므로, `notify.py`만 수정하면 반영되지 않는다. 네 YAML도 함께 고친다.
파일은 GitHub의 실제 기본 브랜치에 있어야 `workflow_run`이 작동한다.
이전 확인 기준 FE는 dev, BE/AI/CLOUD는 main이며 설정에서 재확인한다.
원본 CI/CD와 다른 기본 브랜치에서 실행될 수 있으므로 알림의 브랜치·커밋은 원본 실행 정보를 사용한다.

원격 확인은 리뷰·병합 후 실제 CI 실패에서 확인한다.
배포 실패를 만들기 위해 운영 서버를 고의로 중단하지 않는다.
이후 배포 단계 이름이나 롤백 문구를 바꾸면 `notify.py`도 함께 갱신한다.
다른 방식의 신규 배포는 실패 알림은 보내지만 SSH 배포 인식·롤백 해석은 추가 구현이 필요하다.

## 공식 근거

- [workflow_run 이벤트 및 기본 브랜치 요구사항](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)
- [작업 및 로그 조회 API](https://docs.github.com/en/rest/actions/workflow-jobs)
- [워크플로 보안 지침](https://docs.github.com/en/actions/reference/security/secure-use)

확인일: 2026-09-24.
