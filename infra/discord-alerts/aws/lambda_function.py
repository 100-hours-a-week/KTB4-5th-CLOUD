"""CloudWatch의 SNS 알림을 Discord로 전달한다."""

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def build_payload(alarm, mention_ids):
    state = alarm.get("NewStateValue")
    if state != "ALARM" and not (
        state == "OK" and alarm.get("OldStateValue") == "ALARM"
    ):
        return None
    failed = state == "ALARM"
    arn = alarm["AlarmArn"].split(":", 5)
    region = arn[3]
    name = alarm["AlarmName"]
    instances = [
        item["value"] for item in alarm.get("Trigger", {}).get("Dimensions", [])
        if item.get("name") == "InstanceId"
    ]
    # 이름이 -ticket으로 끝나는 알람은 업무 시간에 확인할 알림이므로 멘션하지 않는다.
    # 그 외(-page 포함, 기존 알람)는 이전과 같이 장애 시 멘션한다.
    ticket = name.endswith("-ticket")
    users = list(dict.fromkeys(mention_ids)) if failed and not ticket else []
    return {
        "content": " ".join(f"<@{user}>" for user in users),
        "allowed_mentions": {"parse": [], "users": users},
        "embeds": [{
            "title": ("🚨 장애 감지: " if failed else "✅ 복구: ") + name[:220],
            "url": f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}"
                   f"#alarmsV2:alarm/{quote(name, safe='')}",
            "color": 15158332 if failed else 3066993,
            "description": str(alarm.get("NewStateReason", "원인 정보 없음"))[:3000],
            "fields": [
                {"name": "상태", "value": f"{alarm.get('OldStateValue', '?')} → {state}"},
                {"name": "심각도", "value": "확인 필요(멘션 없음)" if ticket else "즉시 대응"},
                {"name": "인스턴스", "value": ", ".join(instances)[:1024] or "정보 없음"},
                {"name": "발생 시각", "value": str(alarm.get("StateChangeTime", "정보 없음"))[:100]},
            ],
        }],
    }


def get_webhook():
    # 실행 시마다 읽으므로 웹훅 교체 시 재배포하지 않아도 된다.
    import boto3
    result = boto3.client("secretsmanager").get_secret_value(
        SecretId=os.environ["WEBHOOK_SECRET_ARN"]
    )
    webhook = json.loads(result["SecretString"])["discord_webhook_url"]
    if not re.fullmatch(r"https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+", webhook):
        raise ValueError("Discord 웹훅 주소 형식이 올바르지 않습니다")
    return webhook


def send(webhook, payload):
    request = Request(
        webhook + "?wait=true",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Damuckja-CloudWatch/1.0"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError("Discord 전송 응답이 성공이 아닙니다")
    except HTTPError as error:
        # 예외의 URL에는 웹훅 토큰이 있으므로 원본 예외를 출력하지 않는다.
        raise RuntimeError(f"Discord 전송 실패: HTTP {error.code}") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("Discord 연결 실패 또는 시간 초과") from None


def lambda_handler(event, context):
    users = [value.strip() for value in os.environ.get("DISCORD_USER_IDS", "").split(",") if value.strip()]
    if len(users) > 50 or any(not re.fullmatch(r"[0-9]{15,22}", user) for user in users):
        raise ValueError("Discord 사용자 ID는 최대 50개의 숫자 ID여야 합니다")
    if event.get("source") == "aws.ec2":
        return handle_spot(event, users)
    sent = 0
    webhook = None
    for record in event["Records"]:
        if record.get("EventSource") != "aws:sns" or record["Sns"]["TopicArn"] != os.environ["TOPIC_ARN"]:
            raise ValueError("허용하지 않은 알림 출처입니다")
        alarm = json.loads(record["Sns"]["Message"])
        allowed_names = json.loads(os.environ.get("ALLOWED_ALARM_NAMES", "null"))
        if allowed_names is not None and alarm.get("AlarmName") not in allowed_names:
            continue
        payload = build_payload(alarm, users)
        if payload is None:
            continue
        if webhook is None:
            webhook = get_webhook()
        send(webhook, payload)
        sent += 1
    return {"sent": sent}


def handle_spot(event, users):
    # Spot 중단 예고만 처리하며 일반적인 EC2 정지는 처리하지 않는다.
    if event.get("detail-type") != "EC2 Spot Instance Interruption Warning":
        return {"sent": 0}
    detail = event.get("detail", {})
    instance = detail.get("instance-id")
    allowed = os.environ.get("SPOT_INSTANCE_IDS", "").split(",")
    if not instance or instance not in allowed:
        return {"sent": 0}
    if event.get("account") != os.environ.get("EXPECTED_ACCOUNT") or event.get("region") != os.environ.get("EXPECTED_REGION"):
        raise ValueError("허용하지 않은 AWS 계정 또는 리전입니다")
    action = detail.get("instance-action")
    if action not in ("stop", "terminate", "hibernate"):
        raise ValueError("알 수 없는 Spot 중단 동작입니다")
    users = list(dict.fromkeys(users))
    region = event["region"]
    payload = {
        "content": " ".join(f"<@{user}>" for user in users),
        "allowed_mentions": {"parse": [], "users": users},
        "embeds": [{
            "title": "🚨 Spot 인스턴스 중단 예고",
            "color": 15158332,
            "description": ("최대 절전 모드 전환이 시작됩니다. 2분의 사전 예고 시간이 없습니다."
                            if action == "hibernate" else
                            "AWS에서 Spot 중단 예고를 보냈습니다. 일반적으로 중단 약 2분 전이며, 알림 전달 지연으로 남은 시간이 더 짧을 수 있습니다."),
            "url": f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#InstanceDetails:instanceId={quote(instance, safe='')}",
            "fields": [
                {"name": "인스턴스", "value": instance},
                {"name": "예정 동작", "value": {"stop": "정지", "terminate": "종료·삭제", "hibernate": "최대 절전"}[action]},
                {"name": "이벤트 발생 시각", "value": str(event.get("time", "정보 없음"))[:100]},
            ],
        }],
    }
    send(get_webhook(), payload)
    return {"sent": 1}
