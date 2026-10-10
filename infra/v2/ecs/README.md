# dev ECS 환경변수 자동 주입

`ecs-deploy.yml`은 Task Definition 등록 전에 `scripts/render_ecs_secrets.py`를 실행한다.
FE·BE의 `secrets` 목록은 매 배포 시 SSM에서 다시 생성하므로 JSON의 기존 목록을 직접 수정할 필요가 없다.
nginx·redis의 명시적 설정은 그대로 사용한다.

| 서비스 | SSM 경로 | 수집 범위 |
|---|---|---|
| frontend | `/dameokja-v2-dev-fe/` | 바로 아래 파라미터 |
| backend | `/dameokja-v2-dev/` | 바로 아래 파라미터 |

예를 들어 `/dameokja-v2-dev/AI_BASE_URL`은 `AI_BASE_URL`로 주입하지만
`/dameokja-v2-dev/nginx/default.conf`는 BE에 주입하지 않는다.
이름은 영문자 또는 밑줄로 시작하고 영문자·숫자·밑줄만 사용한다.
Task JSON의 고정 `environment`와 같은 이름을 등록하면 중복 설정으로 배포가 실패한다.

## 등록과 반영

1. 해당 서비스 경로에 값을 등록한다. 비밀값은 SecureString으로 저장한다.
2. 앱 CI를 통한 다음 배포 또는 CLOUD의 `v2 dev Task Definition 반영` 수동 실행으로 반영한다.
   수동 실행은 현재 구성상 nginx·FE·BE·redis 전체를 대상으로 한다.
3. 값은 ECS가 새 Task를 시작할 때 가져온다. SSM 변경만으로 실행 중인 Task가 바뀌지는 않는다.

변수를 삭제하면 다음 배포의 참조 목록에서도 제거된다. 목록 전체가 비어 있거나 조회가 실패하면
환경설정 없이 배포되는 것을 방지하기 위해 Task Definition 등록 전에 중단한다.
FE의 `NEXT_PUBLIC_*`처럼 빌드 시 번들에 포함되는 값은 이 실행 환경변수 주입과 별개로 CI에서 관리한다.

## 권한과 적용 순서

- CD는 `ssm:DescribeParameters`의 `Path/OneLevel` 필터로 이름만 조회한다. 값 조회·복호화 API는 호출하지 않는다.
- `DescribeParameters`는 리소스별 IAM 제한을 지원하지 않아 `Resource: "*"`가 필요하다.
  이 권한은 다른 경로의 메타데이터 조회도 허용하지만, 파라미터 값 읽기 권한을 부여하지 않는다.
- ECS Task Execution Role에는 두 경로의 `ssm:GetParameters` 권한이 이미 있다.
  고객 관리 KMS 키를 쓰는 경우 해당 키의 복호화 권한도 별도로 필요하다.
- 이 변경을 병합한 후 `v2 인프라 CloudFormation`의 IAM 갱신 완료를 확인하고 앱을 배포한다.
  IAM 적용 전에 실행하면 목록 조회에서 실패하며 기존 ECS Service는 변경하지 않는다.
- 등록할 환경변수는 SSM 경로로 결정된다. 해당 경로의 쓰기 권한은 앱 설정 변경 권한으로 관리한다.

기존 `ecs-deploy.yml`은 재사용 워크플로이므로 FE·BE 호출에서도 같은 로직이 실행된다.
이번 변경은 기존 트리거와 배포 동시성 방식을 유지한다.

## 검증

`python3 -m unittest discover -s tests -p 'test_ecs_secrets.py' -v`

실제 AWS 값 없이 서비스별 경로 격리, nginx 하위 경로 제외, 삭제 반영, 조회 실패,
고정 환경변수 충돌 및 다른 컨테이너 설정 보존을 검증한다.

## 근거

- [DescribeParameters](https://docs.aws.amazon.com/systems-manager/latest/APIReference/API_DescribeParameters.html)
- [CLI 경로 필터와 자동 페이지 처리](https://docs.aws.amazon.com/cli/latest/reference/ssm/describe-parameters.html)
- [SSM IAM 지원 범위](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ssm.html)

확인일: 2026-10-09
