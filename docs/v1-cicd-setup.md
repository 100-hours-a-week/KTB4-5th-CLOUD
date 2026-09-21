# v1 CI/CD 실행 안내

## 적용 범위와 현재 상태

- BE 저장소: `100-hours-a-week/KTB4-5th-BE`, dev 기준. Java 25, Gradle Wrapper, MySQL Testcontainers 테스트 후 JAR와 ARM64 이미지를 만든다.
- FE 저장소: `100-hours-a-week/KTB4-5th-FE`, dev 기준. pnpm 12.4.1, Node 24로 Docker 내부에서 Next.js를 빌드한다. 사용자 결정에 따라 초기에는 단위 테스트를 실행하지 않는다.
- CLOUD 저장소: 서버용 Compose·Nginx·공통 배포 스크립트를 관리한다.
- 현재 변경은 로컬 파일이다. 원격 푸시, GitHub 설정, 실제 서버 배포는 아직 수행하지 않았다.
- FE 잠금 파일은 패키지 관리자와 앱 의존성을 별도 YAML 문서로 포함한다. 기존 잠금 파일을 유지하며, pnpm 12.4.1의 `--frozen-lockfile`로 설치한다. 로컬 패키지 설치 실행이 승인되지 않아 실제 의존성 설치·앱 빌드는 아직 검증하지 못했다.

## 트리거와 배포 대상

각 앱 저장소의 `.github/workflows/v1-cicd.yml`을 사용한다.

| 이벤트 | 수행 작업 |
|---|---|
| dev/main 대상 PR | BE 테스트·JAR 빌드 또는 FE 빌드, ARM64 Docker 빌드. 이미지 푸시·배포 없음 |
| dev 푸시 | 검증 → GHCR 푸시 → dev 자동 배포 |
| main 푸시 | 검증 → GHCR 푸시 → CD_ENABLED=true일 때만 prod 자동 배포 |

이미지 이름은 `ghcr.io/100-hours-a-week/ktb4-5th-be:<Git SHA>`, `ghcr.io/100-hours-a-week/ktb4-5th-fe:<Git SHA>`다. 서버에는 같은 실행에서 얻은 `@sha256:...` digest로 배포한다. 브랜치 별칭과 latest는 배포에 사용하지 않는다.

ARM64 실행 가능한 GitHub 호스티드 러너 `ubuntu-24.04-arm`과 ARM64 EC2를 전제로 한다. 다른 CPU 서버라면 이미지 플랫폼과 러너를 함께 변경한다. GHCR 패키지는 처음 게시되었을 때 공개 여부를 직접 확인한다. **소스 저장소가 Public이어도 새 GHCR 패키지 공개 여부를 별도로 설정해야 한다.** Private 패키지라면 서버의 배포 사용자로 GHCR 읽기 로그인을 먼저 한다.

## GitHub 설정

GitHub Environments 권한을 사용하지 않는다. BE·FE 각각 Settings → Secrets and variables → Actions에서 Repository Variables와 Repository Secrets를 등록한다. 워크플로는 환경마다 값이 다른 배포 호스트와 known_hosts만 dev/prod 이름으로 선택하고 나머지는 공통 설정을 사용한다.

### Repository Variables

| 이름 | 내용 |
|---|---|
| CD_ENABLED | 운영 자동 배포 활성화. false이면 main은 빌드·게시만 수행하고 true이면 prod 배포 |
| DEV_DEPLOY_HOST | 개발 SSH 서버 DNS 또는 IPv4 주소 |
| PROD_DEPLOY_HOST | 운영 SSH 서버 DNS 또는 IPv4 주소 |
| DEPLOY_USER | 공통 배포 사용자. Docker 실행과 배포 디렉터리 쓰기 권한 필요 |
| DEPLOY_PORT | 공통 SSH 포트. 생략 시 22 |
| DEPLOY_PATH | 공통 CLOUD 저장소 절대 경로. 공백 없는 경로 |
| COMPOSE_PROJECT | 공통 Compose 프로젝트명 |
| COMPOSE_FILE_NAME | 공통 Compose 파일명 |

현재 공통값은 USER=`ubuntu`, PORT=`22`, PATH=`/home/ubuntu/app`, PROJECT=`app`, FILE=`compose.yaml`이다. dev는 `CD_ENABLED`와 관계없이 항상 배포한다. GitHub Environment 승인이 없으므로 `CD_ENABLED=true`이면 main 푸시가 곧바로 운영 배포로 이어진다. main 브랜치 보호와 필수 PR 검사를 별도로 설정한다.

### Repository Secrets

| 이름 | 내용 |
|---|---|
| SSH_PRIVATE_KEY | 공통 배포 사용자 SSH 개인 키 |
| DEV_SSH_KNOWN_HOSTS | 별도 신뢰 경로로 확인한 개발 서버 공개 호스트 키의 known_hosts 행 |
| PROD_SSH_KNOWN_HOSTS | 별도 신뢰 경로로 확인한 운영 서버 공개 호스트 키의 known_hosts 행 |
| APP_ENV | 해당 저장소 서비스의 실행 환경설정. FE 저장소와 BE 저장소가 서로 다른 값을 가짐 |

FE 저장소의 `APP_ENV`는 `env/frontend.env.example`, BE 저장소의 `APP_ENV`는 `env/backend.env.example` 형식으로 등록한다. 두 저장소는 Secret 저장 공간이 분리되어 있으므로 같은 이름을 사용해도 값이 섞이지 않는다. 각 설정은 한 줄 `KEY=VALUE` 형식이어야 하고 이미지 키는 넣지 않는다.

BE의 `APP_ENV`는 Spring Boot와 MySQL 설정을 한 파일에서 관리한다. 개발 Compose는 데이터소스 주소만 `mysql:3306/app`으로 덮어쓰고, 운영 Compose는 Secret에 등록한 운영 DB EC2 주소를 사용한다. DB 비밀번호 변경은 DB 계정 변경과 함께 별도로 조율한다. MySQL 초기화 환경변수만 바꿔도 기존 DB 계정 비밀번호가 자동 변경되는 것은 아니다.

### FE Repository Variables

| 이름 | 내용 |
|---|---|
| DEV_API_BASE_URL | 실제 개발 API origin. 예: https://개발도메인 |
| PROD_API_BASE_URL | https://dameokja.com |

현재 FE 공통 클라이언트는 origin 뒤에 API path를 붙인다. API path에 이미 /api가 있다면 위 값에 /api를 중복해서 넣지 않는다. 공개 주소는 빌드 시점에 반영되므로 변경 후에는 이미지를 다시 빌드한다. PR에서는 외부 호출용이 아닌 빌드 확인용 주소를 사용한다.

## 서버 최초 준비

Ubuntu 서버에 Docker Engine·Compose 플러그인, bash, python3, flock이 필요하다. 배포 사용자는 sudo 입력 없이 Docker를 실행할 수 있어야 한다. SSH 방화벽은 실제 실행 러너가 접근 가능한 경로로 준비한다.

1. 서버에 이 CLOUD 저장소의 변경 파일을 반영한다. 배포 스크립트는 앱 워크플로가 자동으로 내려받지 않는다.
2. 실제 운영 디렉터리에서 `.env.example`을 `.env`로 복사하고 실제 이미지 SHA와 인증서 경로를 입력한 뒤 `chmod 600 .env`를 실행한다. 서비스 설정은 첫 배포 때 각각 `env/frontend.env`, `env/backend.env`로 생성된다.
3. dev는 `nginx-dev/default.conf`와 `dev.dameokja.com` 인증서를 사용하고, prod는 `nginx-prod/default.conf`와 `dameokja.com` 인증서를 사용한다. 두 환경의 Nginx 디렉터리를 서로 바꾸지 않는다.
4. BE의 `src/main/resources/db/schema.sql`과 동일한 파일을 개발 서버의 `/home/ubuntu/app/db/schema.sql`에 배치한다. 개발 Compose는 이 파일을 MySQL 초기화 디렉터리에 읽기 전용으로 연결한다.
5. prod DB에는 같은 SQL을 검토하여 적용한다. 기존 데이터가 있다면 해당 SQL을 무작정 재실행하지 않는다. 운영 Compose의 validate는 스키마를 생성하지 않는다.
6. dev는 먼저 MySQL을 시작하고 healthy 상태를 확인한 뒤 앱 CD를 활성화한다.

```bash
docker compose -p 실제프로젝트명 --env-file .env -f dev-compose.yaml up -d --wait mysql
```

개발 MySQL은 합의된 정책에 따라 영속 볼륨 없이 tmpfs를 사용한다. 빈 데이터 디렉터리로 처음 기동할 때 `/home/ubuntu/app/db/schema.sql`을 실행하고, 백엔드는 `ddl-auto=validate`로 매핑을 검사한다. BE 배포 시 스크립트는 SQL 파일이 존재하고 비어 있지 않은지 확인한 후 MySQL을 시작하고 healthy 상태를 기다린다. 이미 초기화된 컨테이너에는 SQL을 다시 실행하지 않으므로 일반 BE 배포마다 데이터를 초기화하지 않는다. SQL 내용을 변경했다면 개발 MySQL 컨테이너를 명시적으로 재생성해야 새 DDL이 적용된다. MySQL 컨테이너가 제거되거나 서버가 재시작되면 개발 데이터가 사라진다. 서버에 남아 있는 기존 mysql_data 볼륨은 새 Compose에서 사용하지 않으며 자동 삭제하지도 않는다.

Nginx 최초 실행·검사와 인증서 갱신은 README를 따른다. 개발용 dev-compose.yaml을 기존 서버의 compose.yaml로 반영한다면 COMPOSE_FILE_NAME=compose.yaml을 등록하고 서버 명령에도 같은 파일명을 사용한다. 개발 Compose는 `nginx-dev/default.conf`, 운영 Compose는 `nginx-prod/default.conf`를 자동으로 마운트한다. 두 설정 모두 frontend:3000, backend:8080 및 Swagger 경로를 연결한다.

## 배포와 실패 처리

서버에서 scripts/deploy-v1.sh가 아래 순서로 동작한다.

1. 서버 공통 파일 잠금으로 FE·BE 동시 배포를 직렬화한다.
2. BE 개발 배포라면 `db/schema.sql`을 확인하고 MySQL을 시작한 뒤 healthy 상태를 기다린다.
3. 현재 이미지 설정과 배포 대상 서비스의 환경 파일을 보존한다.
4. 신규 서비스 설정 검사와 이미지 pull에 성공한 뒤 `.env`와 해당 서비스 환경 파일을 교체한다.
5. 대상 서비스만 up -d --no-build --no-deps로 교체한다.
6. 최대 240초간 Docker health 상태를 확인한다.
7. 실패하면 이전 이미지와 해당 서비스 환경 파일을 함께 복구하고 health를 다시 확인한다. 복구에 성공해도 배포 작업은 실패로 끝난다.

Dockerfile에는 헬스체크를 포함하지 않는다. 환경별 Compose가 BE의 /v3/api-docs 응답과 FE의 / 응답을 확인한다. DB 지속 연결이나 업무 API 정상 여부까지 보장하는 검사는 아니다. Swagger 인증·경로·활성화 설정이 바뀌면 BE 상태 확인 경로도 함께 변경해야 한다.

Compose 헬스체크가 누락되면 롤백 후 정상 여부를 자동 확정할 수 없다. 최초 전환 전에 이전 이미지의 복구 가능성을 확인한다. 첫 배포 실패에는 돌아갈 이미지가 없으므로 실패 컨테이너를 중지하고 이전 .env만 복구한다. DB 스키마는 자동 롤백하지 않는다.

단일 앱 컨테이너 교체 방식이므로 짧은 중단이 발생한다. CI/CD는 Nginx 설정 변경을 자동 배포하거나 무중단 전환을 수행하지 않는다.

## 검증과 적용 순서

1. CLOUD 변경을 리뷰하고 서버에 반영한다.
2. BE·FE 변경을 각각 dev 대상 PR로 올려 CI 결과를 확인한다. FE의 고정 잠금 파일 설치와 BE의 Testcontainers 실행을 포함해 확인한다.
3. GitHub 환경·Secret·필수 CI 상태 검사와 GHCR 공개 범위를 설정한다.
4. dev CD를 활성화하고 실제 정상 배포와 실패 복구를 확인한다.
5. prod 승인 설정을 확인하고 main으로 변경을 반영한다.

로컬에서는 배포 스크립트의 bash 문법, prod/dev Compose 파싱, 모의 Docker를 이용한 배포 상태 전이 테스트를 확인한다. 모의 테스트는 실제 ARM 이미지 기동·SSH·Nginx·DB 연결·파일 잠금 경쟁을 검증하지 않는다.

```bash
bash -n scripts/deploy-v1.sh
python3 tests/test_deploy.py
docker compose --env-file .env.example -f prod-compose.yaml config --quiet
docker compose --env-file .env.example -f dev-compose.yaml config --quiet
```

## 참고

- [GitHub Actions Variables](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-variables)
- [Docker의 GitHub Actions 이미지 빌드](https://docs.docker.com/build/ci/github-actions/)
- [Next.js 배포 방식](https://nextjs.org/docs/app/getting-started/deploying)

확인일: 2026-09-18. 트리거·SSH·GHCR·실패 복구 정책은 제공된 v1 설계 문서를 따르고, FE 빌드 검증 우선은 이번 사용자 결정에 따른다.
