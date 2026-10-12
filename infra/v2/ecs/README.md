# dev ECS 환경변수

컨테이너 환경변수는 `infra/v2/ecs/dev/<service>.json`의 `secrets`에 키마다 직접 적는다.
ECS가 Task를 시작할 때 `valueFrom`의 SSM 값을 읽어 `name` 환경변수로 넣는다.

```json
{ "name": "SERVER_PORT", "valueFrom": "/dameokja-v2-dev/SERVER_PORT" }
```

| 서비스 | SSM 경로 |
|---|---|
| backend | `/dameokja-v2-dev/` |
| frontend | `/dameokja-v2-dev-fe/` |
| ai | `/dameokja-v2-dev-ai/` |

## 변경할 때

- 키 추가·삭제: SSM에 만들거나 지우고, 같은 PR에서 JSON의 `secrets`에 한 줄 추가·삭제한다.
  main에 머지되면 `v2 dev Task Definition 반영`이 바뀐 서비스만 다시 배포한다.
  JSON에 있는데 SSM에 없는 키가 있으면 Task가 시작하지 못한다(이전 버전으로 롤백).
- 값만 수정: JSON은 그대로 두고 서비스를 재시작한다(ECS 콘솔 "새 배포 강제").
  값은 Task가 시작할 때 읽으므로, 재시작 전까지 실행 중인 컨테이너는 이전 값을 쓴다.

비밀값은 SecureString으로 저장한다. FE의 `NEXT_PUBLIC_*`처럼 빌드 때 이미지에 들어가는 값은 FE CI에서 관리한다.
