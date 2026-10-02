# EC2·Spot Discord 알림 설정 관리

이 문서는 알림에 사용하는 시크릿과 환경 변수의 저장 위치, 수정 방법을 정리한다.
기준은 저장소 코드와 사용자가 지정한 구성이다. 실제 AWS 배포 상태와 값은 콘솔에서 확인한다.
웹훅 URL과 Secret의 실제 내용은 Git에 저장하지 않는다.

## 현재 구성

| 항목 | 위치 또는 이름 |
|---|---|
| AWS 리전 | 서울 `ap-northeast-2` |
| CloudFormation 스택 | `damuckja-v1-discord-alerts` |
| Secrets Manager 비밀 이름 | `damuckja/v1/discord-webhook` |
| 비밀 안의 키 | `discord_webhook_url` |
| 사용자가 지정한 Spot 대상 | `i-007f9683d847fe700` — 실제 등록값은 스택 Parameters 확인 |
| Lambda 실제 함수 이름 | 스택 → Outputs → `FunctionName` |
| SNS 토픽 ARN | 스택 → Outputs → `TopicArn` |
| 실패 보관 SQS URL | 스택 → Outputs → `FailureQueueUrl` |
| EventBridge 규칙 | 스택 → Resources → `SpotWarningRule`의 Physical ID |

Spot 예고는 EventBridge → Lambda → Discord로 전달된다.
CPU·상태 검사 알람은 CloudWatch Alarm → SNS → Lambda → Discord로 전달된다.
Spot 예고는 일반적으로 회수 약 2분 전이지만 전달과 도착 시점은 보장되지 않는다.
수동 EC2 정지는 Spot 예고가 아니며 최대 절전 동작에는 2분의 예고 시간이 없다.

## 1. 실제 시크릿은 Secrets Manager에 있다

경로: **AWS Secrets Manager → Secrets → damuckja/v1/discord-webhook → Retrieve secret value**.

| 키 | 값의 용도 | 변경 위치 |
|---|---|---|
| `discord_webhook_url` | 알림을 보낼 Discord 채널의 웹훅 URL | 해당 비밀의 값 편집 |

이름과 키는 서로 다르다. `damuckja/v1/discord-webhook`은 비밀 이름이고,
그 안의 `discord_webhook_url` 값이 실제 전송 주소다.

- Lambda는 호출 시 비밀을 읽는다. 같은 비밀의 URL만 교체하면 코드 재배포는 필요 없다.
- 비밀을 새로 만들면 ARN이 달라지므로 CloudFormation의 `WebhookSecretArn`을 업데이트한다.
- 템플릿에는 URL 대신 ARN만 전달한다. ARN은 URL 자체가 아니다.
- 기본 `aws/secretsmanager` 암호화 키 기준이다. 별도 KMS 키 사용 시 복호화 권한도 필요하다.

## 2. 직접 관리할 입력값은 CloudFormation Parameters에 있다

경로: **CloudFormation → Stacks → damuckja-v1-discord-alerts → Parameters**.
수정은 **Update stack → Make a direct update**로 진행한다.
파라미터만 바꾸면 현재 템플릿을 유지하고, 코드가 바뀌면 새 템플릿으로 교체한다.

| 파라미터 | 입력 내용 | 적용되는 곳 |
|---|---|---|
| `WebhookSecretArn` | 위 비밀의 전체 ARN(실제 값은 Secrets Manager에서 복사) | Lambda의 `WEBHOOK_SECRET_ARN` 및 읽기 권한 |
| `DiscordUserIds` | 멘션할 개인 Discord 숫자 ID. 여러 명은 쉼표로 구분, 공백 없이 입력 | Lambda의 `DISCORD_USER_IDS` |
| `SpotInstanceIds` | 감시할 Spot 인스턴스 ID. 여러 대는 쉼표로 구분, 공백 없이 입력 | Lambda의 `SPOT_INSTANCE_IDS` 및 EventBridge 감지 대상 |

`DiscordUserIds`를 비우면 메시지만 보내고 멘션하지 않는다.
`SpotInstanceIds`를 비우면 Spot 규칙을 생성하지 않으며 업데이트 시 기존 규칙은 제거된다.
인스턴스를 교체해 ID가 바뀌면 `SpotInstanceIds`도 갱신해야 한다.
기존 알람 모드에서는 `CpuThreshold`, `EnableNotifications` 파라미터가 없다.
이 둘은 신규 CPU·상태 검사 알람까지 템플릿으로 만드는 모드에서만 사용한다.

## 3. 실행에 쓰이는 환경 변수는 Lambda에 있다

경로: **Lambda → 스택 Outputs의 FunctionName → Configuration → Environment variables**.
아래 변수는 템플릿이 생성한다. 확인은 Lambda에서 하되 변경은 원본 설정이나 스택 파라미터에서 한다.
콘솔에서 직접 바꾸면 CloudFormation과 실제 설정이 달라질 수 있다.

| Lambda 변수 | 값의 출처 | 변경할 원본 |
|---|---|---|
| `WEBHOOK_SECRET_ARN` | `WebhookSecretArn` 파라미터 | CloudFormation Parameters |
| `DISCORD_USER_IDS` | `DiscordUserIds` 파라미터 | CloudFormation Parameters |
| `SPOT_INSTANCE_IDS` | `SpotInstanceIds` 파라미터 | CloudFormation Parameters |
| `TOPIC_ARN` | 스택이 생성한 SNS 토픽 | 자동 관리, 직접 입력하지 않음 |
| `ALLOWED_ALARM_NAMES` | `existing-alarms.json`의 이름 목록을 JSON 문자열로 변환 | 파일 수정 → 템플릿 재생성 → 스택 업데이트 |
| `EXPECTED_ACCOUNT` | 배포한 AWS 계정 ID | 자동 관리 |
| `EXPECTED_REGION` | 배포한 AWS 리전 | 자동 관리 |

`discord_webhook_url`은 Lambda 환경 변수가 아니라 Secrets Manager 내부 키다.
`ALLOWED_ALARM_NAMES`는 기존 알람 모드에서만 생성되며 Spot 이벤트에는 적용되지 않는다.
한글 알람은 SNS 정책에서 ASCII 패턴으로 처리하고 Lambda에서 정확한 이름을 다시 검사한다.

## 4. 저장소에서 관리하는 파일

| 파일 | 역할 |
|---|---|
| `lambda_function.py` | CloudWatch·Spot 이벤트 처리와 Discord 전송 코드 |
| `build_template.py` | Python 소스와 리소스 설정을 포함한 CloudFormation JSON 생성기 |
| `existing-alarms.json` | 연결할 기존 CloudWatch 알람의 정확한 이름 목록 |
| `instances.example.json` | 신규 CPU·상태 검사 알람 생성 모드용 샘플. 현재 기존 알람 모드에서는 사용하지 않음 |
| `generated/discord-alerts.template.json` | 생성 결과물. Git 제외, 배포 전에 다시 생성 |

샘플 JSON을 수정해도 AWS 환경 변수는 바뀌지 않는다.
Git에서 관리하는 코드를 수정해도 AWS에는 자동 반영되지 않는다.

## 파일이 연결되는 순서

```text
lambda_function.py + existing-alarms.json
                ↓ build_template.py
  generated/discord-alerts.template.json
                ↓ CloudFormation 업데이트
       Lambda·SNS·EventBridge 배포
```

- `lambda_function.py`는 메시지와 이벤트 처리의 원본 코드다.
- `build_template.py`는 코드와 AWS 리소스 정의를 배포 파일로 조합한다.
- `existing-alarms.json`은 기존 알람의 이름 허용 목록이며 임계값을 저장하지 않는다.
- `instances.example.json`은 새 CPU·상태 검사 알람까지 만들 때 쓰는 예시다. 기존 알람 모드에서는 사용하지 않는다.
- `generated/discord-alerts.template.json`은 직접 편집하지 않고 다시 생성한다. `--output`을 생략해도 이 위치에 생성된다.

Spot 대상 ID는 두 입력 JSON과 별개이며 CloudFormation의 `SpotInstanceIds`에서 관리한다.

## 5. 무엇을 바꿀 때 어디를 수정하는가

| 변경 사항 | 절차 |
|---|---|
| Discord 채널/웹훅 교체 | Secrets Manager의 `discord_webhook_url` 값 변경 |
| 새 Secret으로 교체 | `WebhookSecretArn` 파라미터 변경 |
| 멘션 담당자 변경 | `DiscordUserIds` 파라미터 변경 |
| Spot 대상 추가·교체 | `SpotInstanceIds` 파라미터 변경 |
| 기존 CloudWatch 알람 추가·이름 변경 | `existing-alarms.json` 수정, 템플릿 재생성, 스택 업데이트, 해당 알람의 In alarm/OK에 SNS 연결 |
| 메시지 내용 또는 처리 코드 변경 | `lambda_function.py` 수정, 템플릿 재생성, 스택 업데이트 |

## 6. 템플릿 재생성과 배포

CLOUD 저장소 루트에서 실행한다.

```powershell
python infra/discord-alerts/aws/build_template.py infra/discord-alerts/aws/existing-alarms.json --existing-alarms --output infra/discord-alerts/aws/generated/discord-alerts.template.json
```

CloudFormation에서 해당 스택을 선택한 뒤:

1. **Update stack → Make a direct update**.
2. **Replace current template → Upload a template file**에서 생성된 JSON 업로드.
3. 기존 파라미터 유지 또는 필요한 값만 변경.
4. IAM 리소스 확인 후 제출하고 `UPDATE_COMPLETE` 확인.

템플릿의 `Resources.Function.Properties.Code.ZipFile`에 Python 코드 전체가 들어간다.
CloudFormation이 이를 Lambda의 `index.py`로 배포하므로 코드를 별도 업로드할 필요가 없다.

콘솔 테스트는 실제 Discord 메시지를 보낼 수 있다. 계정·인스턴스·토픽과 알람 이름을 실제 설정에 맞춘다.
`sent: 0`이면 허용 목록과 이벤트 상태를 확인한다.
Spot 테스트에서 `KeyError: Records`이면 배포된 코드에 Spot 분기 처리가 있는지 확인한다.

## GitHub 멘션 알림

별도 GitHub Secrets와 워크플로는 [GitHub 알림 안내](../github/mentions/README.md)에서 관리한다.
