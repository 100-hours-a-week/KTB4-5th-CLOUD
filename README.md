# 앱 서버 GHCR 배포 및 별도 EC2 MySQL 연결

## v1 CI/CD

FE·BE의 GitHub Actions, 환경별 Secrets·Variables, 개발 서버 준비, 배포·복구 절차는 [v1 CI/CD 실행 안내](docs/v1-cicd-setup.md)를 따른다. 공통 배포 스크립트는 `scripts/deploy-v1.sh`이며 개발 환경은 `dev-compose.yaml`을 사용한다. dev는 항상 자동 배포하고, prod는 GitHub의 `CD_ENABLED=true`일 때만 자동 배포한다.

`prod-compose.yaml`은 앱 EC2에서 Nginx, 이미 빌드된 Next.js(3000), Spring Boot(8080), Certbot을 실행한다. MySQL은 별도 EC2에서 운영하며 Compose에 포함하지 않는다. 서버 빌드는 하지 않는다. 외부에는 Nginx의 80/443만 공개한다.

## 최초 설정

이 폴더를 서버에 배치하고 해당 폴더에서 실행한다.

```bash
cp .env.example .env
chmod 600 .env
```

`.env`의 GHCR 주소와 인증서 경로를 실제 값으로 수정한다. FE 실행 설정은 FE 저장소의 `APP_ENV`에서 `env/frontend.env`로, BE와 DB 설정은 BE 저장소의 `APP_ENV`에서 `env/backend.env`로 배포된다. 인증서는 이미 발급된 `dameokja.com` 인증서를 사용하며, `LETSENCRYPT_DIR`에는 `live/`와 `archive/`가 포함된 기존 Certbot 저장 폴더를 지정한다. 갱신 시에는 저장된 webroot 경로 `/var/www/certbot`을 사용한다.

BE 저장소의 `APP_ENV`에서 `SPRING_DATASOURCE_URL`을 DB EC2의 프라이빗 IP 또는 프라이빗 DNS로 지정하고 기존 DB 이름과 애플리케이션 계정을 입력한다. 운영 주소에는 `localhost`나 Compose 서비스명 `mysql`을 사용하지 않는다.

- 앱 EC2에서 DB EC2까지 VPC 라우팅이 가능해야 한다.
- DB 보안 그룹의 TCP 3306 소스를 앱 EC2 보안 그룹으로 제한하고 앱 측 아웃바운드도 허용한다.
- MySQL이 프라이빗 인터페이스에서 연결을 수신하고 앱 서버에서 접속 가능한 DB 계정 권한이 설정돼 있어야 한다.
- 원격 DB는 Compose의 `depends_on`/healthcheck로 시작을 기다리지 않는다. BE 배포 전에 DB 연결과 스키마를 준비하고, 실행 후 BE 로그로 연결을 확인한다. 애플리케이션의 연결 재시도/복구도 구성한다.
- DB 백업, 복구, 패치, 스키마 관리는 별도 DB 서버에서 수행한다. 이 파일 변경은 기존 컨테이너나 볼륨을 삭제하지 않는다.

기존 Nginx 컨테이너 이름이 `ubuntu-nginx-1`이면 기존 Compose 프로젝트 이름은 일반적으로 `ubuntu`다. 프로젝트 이름을 유지하려면 아래 모든 명령에 `-p ubuntu`를 추가한다. 다른 프로젝트로 배포할 경우 기존 Nginx를 먼저 중지하여 80/443 충돌을 방지한다.

```bash
sudo docker compose -f prod-compose.yaml config --quiet
sudo docker compose -f prod-compose.yaml run --rm --no-deps nginx nginx -t
sudo docker compose -f prod-compose.yaml up -d nginx
curl -I http://dameokja.com
curl -fsS https://dameokja.com/nginx-health
```

FE/BE가 없을 때 `/`와 `/api`는 502를 반환한다. `/nginx-health`는 Nginx/TLS 확인용이며 앱이나 DB의 정상 여부를 보장하지 않는다.

## GHCR 앱 배포

비공개 패키지는 읽기 권한이 있는 GitHub 계정의 PAT classic (`read:packages`)으로 로그인한다. Password 프롬프트에 토큰을 입력한다. 공개 패키지는 로그인을 생략할 수 있다.

```bash
sudo docker login ghcr.io -u YOUR_GITHUB_USERNAME
sudo docker compose -f prod-compose.yaml --profile app pull frontend backend
sudo docker compose -f prod-compose.yaml --profile app up -d --no-build frontend backend
sudo docker compose -f prod-compose.yaml --profile app logs --tail 50 frontend backend
```

각 명령이 성공한 후 다음 명령을 실행한다. 이미지에는 시작 명령이 포함돼야 하며 EC2 CPU 아키텍처와 호환돼야 한다. Next.js는 `0.0.0.0:3000`, Spring Boot는 `8080`에서 수신한다.

- 브라우저는 `/api/...`를 호출하고, BE는 `/api/...` 경로를 그대로 처리한다.
- Next.js 서버 측 코드는 `API_INTERNAL_URL`을 명시적으로 읽어 사용한다. 이 변수는 Next.js 내장 설정이 아니다.
- `NEXT_PUBLIC_*` 값이 필요하면 CI의 Next.js 빌드 시점에 전달한다. 실행 시점 환경변수만 바꿔도 기존 브라우저 번들이 변경되지는 않는다.
- BE에는 MySQL JDBC 드라이버가 필요하다. DDL/마이그레이션을 적용해야 하며 `ddl-auto=validate`는 테이블을 생성하지 않는다.
- 새 배포는 `.env`의 이미지 태그 변경 후 pull/up으로 수행한다. 단일 인스턴스이므로 교체 중 짧은 중단이 발생할 수 있다. Nginx는 Docker DNS를 재조회한다.
- 이전 이미지로 돌아가도 DB 스키마는 되돌아가지 않는다.

## Swagger 접속 및 SSH 적용

Nginx는 springdoc 기본 경로 `/swagger-ui.html`, `/swagger-ui/`, `/v3/api-docs`와 하위 경로, `/v3/api-docs.yaml`을 백엔드로 전달한다. 접속 주소는 `https://dameokja.com/swagger-ui/index.html`이다. 별도 Swagger 컨테이너나 외부 8080 포트 개방은 필요 없다.

백엔드 이미지에는 Spring Boot 버전에 맞는 springdoc UI 의존성이 포함되어 있어야 하고, 실행 프로필에서 문서 기능이 활성화되어 있어야 한다. Spring Security와 JWT 필터를 사용한다면 문서 접근 정책에 맞게 위 경로를 허용해야 한다. 이 저장소에는 백엔드 소스가 없으므로 해당 설정은 백엔드에서 별도로 확인한다. context-path나 springdoc 경로를 변경한 경우 Nginx 경로도 맞춰야 한다.

운영 Compose는 `nginx-prod/default.conf`를, 개발 Compose는 `nginx-dev/default.conf`를 마운트한다. 운영은 `dameokja.com`, 개발은 `dev.dameokja.com`과 각 도메인의 인증서 경로가 설정되어 있다. 기존 설정은 마운트 디렉터리 밖에 백업한다. 마운트 디렉터리 안에 `.conf` 확장자로 백업하면 Nginx가 함께 로드할 수 있다. 기존 Compose 프로젝트 이름이 `ubuntu`라면 아래 명령에도 `-p ubuntu`를 추가한다.

```bash
sudo docker compose -f prod-compose.yaml exec -T nginx nginx -t
sudo docker compose -f prod-compose.yaml exec -T nginx nginx -s reload
curl -fsS -o /dev/null -w '%{http_code}\n' https://dameokja.com/swagger-ui/index.html
curl -fsS https://dameokja.com/v3/api-docs
```

각 명령이 성공했을 때만 다음 명령을 실행한다. UI는 HTTP 200, API 명세는 OpenAPI JSON 응답을 확인하고 브라우저에서도 문서 로딩과 Try it out을 확인한다. 문서 접근에 인증을 설정했다면 해당 인증 절차를 거쳐 확인한다. Nginx가 실행 중이 아니라면 최초 설정 절차의 `run --rm --no-deps nginx nginx -t`로 검사한 뒤 `up -d nginx`로 시작한다.

Swagger를 백엔드에 새로 추가했다면 새 이미지를 GHCR에 게시하고 `.env`의 `BE_IMAGE`를 갱신한 후 아래 명령으로 백엔드를 배포한다.

```bash
sudo docker compose -f prod-compose.yaml --profile app pull backend
sudo docker compose -f prod-compose.yaml --profile app up -d --no-build backend
sudo docker compose -f prod-compose.yaml --profile app logs --tail 50 backend
```

401/403이면 백엔드 인증 설정, 404이면 springdoc 의존성·활성화·경로, 502이면 백엔드 컨테이너 상태와 Nginx 로그를 확인한다.

## 인증서 갱신

```bash
sudo docker compose -f prod-compose.yaml run --rm certbot renew --dry-run
```

기존 자동 갱신 작업의 디렉터리와 파일명을 이 구성에 맞춰 변경한다. 실제 갱신 작업은 아래 순서이며 테스트 명령만으로 자동 갱신이 등록되지는 않는다.

```bash
sudo docker compose -f prod-compose.yaml run --rm -T certbot renew --quiet
sudo docker compose -f prod-compose.yaml exec -T nginx nginx -t
sudo docker compose -f prod-compose.yaml exec -T nginx nginx -s reload
```

앞 명령이 성공했을 때만 다음 명령을 실행하도록 갱신 스크립트에 `set -e` 또는 `&&`를 사용한다. 기존 자동 갱신이 없다면 별도 timer/cron 등록이 필요하다. `.env`와 인증서 개인 키는 Git에 올리지 않는다.
