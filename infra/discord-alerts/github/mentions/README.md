# GitHub 이슈 멘션 → Discord 파트 채널 알림

## 파일과 실행 위치

`discord-mentions.yml`은 다섯 저장소에서 사용하는 공통 관리본이다.
GitHub Actions는 이 폴더의 YAML을 실행하지 않으므로 수정 후 각 저장소의
`.github/workflows/discord-mentions.yml`에 복사하고 리뷰·병합한다.
실행용 파일은 제거하지 않는다. 코드를 checkout하지 않는 독립 워크플로 구조를 유지한다.

대상 저장소: KTB4-5th-wiki, KTB4-5th-FE, KTB4-5th-BE, KTB4-5th-AI, KTB4-5th-CLOUD.
정리 시점에 다섯 로컬 저장소의 실행용 YAML이 공통 관리본과 동일함을 확인했다.
원격 배포 여부는 별도로 확인한다.

CLOUD 저장소 루트에서 관리본을 실행 위치로 반영하는 명령:

```powershell
Copy-Item -LiteralPath "infra/discord-alerts/github/mentions/discord-mentions.yml" -Destination ".github/workflows/discord-mentions.yml"
git diff -- .github/workflows/discord-mentions.yml
```

다른 저장소도 같은 파일을 각자의 `.github/workflows`에 반영한다.
대상 파일에 미반영 수정이 있으면 먼저 비교하고 병합한다.
워크플로는 해당 저장소의 기본 브랜치에 있어야 이벤트로 실행된다.
이전 확인 기준 FE 기본 브랜치는 dev, wiki/BE/AI/CLOUD는 main이며 실제 설정은 GitHub에서 확인한다.
BE의 dev 브랜치에만 병합하면 기본 브랜치에서 활성화되지 않는다.

## 동작 범위

- 이슈 opened/edited, 이슈 댓글 created/edited를 처리하며 PR 댓글은 제외한다.
- 개인 `@GitHub아이디`를 찾아 매핑된 담당 파트 채널로 보낸다.
- 수정 이벤트에서는 새로 추가한 멘션만 전송한다.
- 코드 블록, 인라인 코드, 인용문, 이메일, 조직 팀 멘션은 제외한다. 팀 멘션 전체 호출은 아직 지원하지 않는다.
- 파트별 대상 중복을 제거하고 지정된 Discord 사용자만 멘션한다.
- 링크는 이슈 또는 댓글로 이동하는 이슈 번호·제목 링크다.
- 전송 실패가 있으면 다른 파트 처리를 계속하고 최종 작업 실패로 표시한다.

## Secrets 저장 위치

각 저장소의 **Settings → Secrets and variables → Actions → Secrets**에 등록한다.
Variables가 아니다. 아래 이름은 AWS Lambda 환경 변수와 별개다.

| Secret | 내용 |
|---|---|
| `DISCORD_USER_MAP` | GitHub 로그인 → Discord 숫자 ID 및 part 매핑 JSON |
| `DISCORD_FE_WEBHOOK_URL` | FE 채널 웹훅 URL |
| `DISCORD_BE_WEBHOOK_URL` | BE 채널 웹훅 URL |
| `DISCORD_AI_WEBHOOK_URL` | AI 채널 웹훅 URL |
| `DISCORD_CLOUD_WEBHOOK_URL` | CLOUD 채널 웹훅 URL |

`user-map.example.json`은 가짜 값으로 만든 형식 예시다. 실제 매핑은 Secrets에 저장한다.
GitHub 로그인에는 `@`를 붙이지 않고, `discord_id`는 숫자로 이루어진 문자열로 입력한다.
`part`는 `fe`, `be`, `ai`, `cloud` 중 하나다.
예전 단일 채널용 `DISCORD_WEBHOOK_URL`은 이 워크플로에서 사용하지 않는다.
AWS의 Secrets Manager를 변경해도 이 값들은 바뀌지 않는다.

## 수정 후 확인

관리본과 실행용 파일을 함께 반영하고 GitHub Actions 실행 로그에서 성공 여부를 확인한다.
기본 브랜치 반영 후 실제 이슈에 등록된 사용자를 멘션하면 실제 Discord 메시지가 전송된다.
메시지에는 담당자 멘션이 포함되며 알림 소리와 수신 여부는 수신자의 Discord·기기 설정에 따른다.

## CI/CD 결과 알림

빌드·배포 실패, 롤백 결과, 선택적 배포 성공 알림은 [CI/CD 알림 안내](../deployment/README.md)를 따른다.
각 파트의 실행용 파일은 `.github/workflows/discord-cicd.yml`이며 기존 이슈 멘션 워크플로와 독립적이다.
기존 웹훅·사용자 매핑 Secrets를 재사용하고, 성공 알림은 Repository Variable `DISCORD_NOTIFY_SUCCESS=true`로 켠다.
