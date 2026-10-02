# BE 애플리케이션 지표 노출 요청 (Actuator → Prometheus)

작성일: 2026-10-02  
목적: Tomcat 스레드 풀, DB 커넥션 풀(HikariCP), JVM 스레드, SLI API 요청 수·처리 시간을 CloudWatch에서 보기 위해, BE가 Prometheus 형식으로 지표를 내놓도록 요청합니다. 수집(CloudWatch Agent)과 대시보드는 인프라 쪽에서 처리합니다.

## 1. 요청 변경 (BE)

**build.gradle**

```gradle
implementation 'io.micrometer:micrometer-registry-prometheus'
```

**application.yml**

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health, prometheus   # prometheus 추가
```

Tomcat 스레드 지표(busy·current·max)에 필요한 `server.tomcat.mbeanregistry.enabled: true`는 BE가 아니라 **인프라 compose의 backend 환경변수**(`SERVER_TOMCAT_MBEANREGISTRY_ENABLED: "true"`)로 켭니다.

- 이유: 비밀이 아니고 dev·prod 값이 같아, Git에 남는 compose에서 관리하면 변경 이력을 보기 쉽고 BE 배포 없이 적용할 수 있습니다.
- 트레이드오프: BE 설정이 인프라 파일에 있어 BE 파트가 놓칠 수 있습니다. BE `application.yml`에 같은 설정을 넣게 되면 compose의 값이 우선하므로, 그때는 compose 쪽 줄을 지워 한 곳에서만 관리합니다.

**SecurityConfig**

```java
.requestMatchers("/actuator/health", "/actuator/health/**", "/actuator/prometheus").permitAll()
```

## 2. 확인 방법 (서버에서)

```bash
curl -s http://127.0.0.1:8080/actuator/prometheus | grep -E "^(tomcat_threads_busy|hikaricp_connections_active|jvm_threads_live|http_server_requests_seconds_count)" | head
```

네 종류 지표가 모두 보이면 됩니다.

## 3. 외부 노출 범위

- 인프라(compose)에서 BE 포트를 `127.0.0.1:8080`으로 **서버 내부에만** 엽니다.
- nginx는 `/api`만 BE로 전달하고 `/actuator`는 전달하지 않아, 외부에서는 접근할 수 없습니다.
- **트레이드오프**: 인증 없이 열지만 서버 안에서만 접근 가능하므로 별도 토큰 관리가 필요 없습니다. 대신 nginx 라우팅이 바뀌어 `/actuator`가 BE로 전달되면 외부에 노출되므로, nginx 설정을 바꿀 때 함께 확인해야 합니다.

## 4. 수집하는 지표 (인프라에서 거름)

| 구분 | Prometheus 지표 | 의미 |
|---|---|---|
| Tomcat 스레드 풀 | `tomcat_threads_busy_threads`, `tomcat_threads_current_threads`, `tomcat_threads_config_max_threads` | 요청 처리 스레드 사용 중·생성됨·최대 |
| HikariCP | `hikaricp_connections_active`, `_idle`, `_pending`, `_max`, `hikaricp_connections_timeout_total` | DB 커넥션 사용·유휴·대기·최대, 획득 타임아웃 |
| JVM 스레드 | `jvm_threads_live_threads`, `_daemon_threads`, `_peak_threads`, `jvm_threads_states_threads{state}` | 스레드 수와 상태별(blocked·waiting 등) |
| HTTP 요청 | `http_server_requests_seconds_count`, `_sum` (SLI API 6개만) | 요청 수, 평균 처리 시간 |

HTTP 요청 지표는 경로·메서드·상태코드마다 따로 생겨 개수가 많아지므로, SLO 대상 API만 골라 보냅니다(CloudWatch 비용 때문).
