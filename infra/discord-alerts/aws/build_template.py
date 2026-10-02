"""인스턴스 목록과 Lambda 소스로 독립 실행 가능한 CloudFormation JSON을 만든다."""

import argparse
import json
from pathlib import Path
import re


def ref(name):
    return {"Ref": name}


def sub(value):
    return {"Fn::Sub": value}


def arn(name):
    return {"Fn::GetAtt": [name, "Arn"]}


def build(instances, existing_alarms=None):
    if existing_alarms is not None:
        if instances or not isinstance(existing_alarms, list) or not 1 <= len(existing_alarms) <= 100:
            raise ValueError("기존 알람 목록은 1~100개이며 신규 인스턴스와 함께 지정할 수 없습니다")
        for name in existing_alarms:
            if not isinstance(name, str) or not 1 <= len(name) <= 255 or any(c in name for c in ("*", "?", "${", "\n", "\r")):
                raise ValueError("기존 알람 이름이 올바르지 않습니다")
    elif not isinstance(instances, list) or not 1 <= len(instances) <= 100:
        raise ValueError("인스턴스 목록은 1~100개여야 합니다")
    names, ids = set(), set()
    for item in instances:
        name, instance_id = item["name"], item["instance_id"]
        if not re.fullmatch(r"[a-zA-Z0-9-]{1,50}", name):
            raise ValueError("서버 이름은 영문, 숫자, 하이픈으로 1~50자여야 합니다")
        if not re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", instance_id):
            raise ValueError("EC2 인스턴스 ID 형식이 올바르지 않습니다")
        if name in names or instance_id in ids:
            raise ValueError("서버 이름과 인스턴스 ID는 중복될 수 없습니다")
        names.add(name)
        ids.add(instance_id)

    resources = {
        "Topic": {"Type": "AWS::SNS::Topic"},
        "TopicPolicy": {
            "Type": "AWS::SNS::TopicPolicy",
            "Properties": {
                "Topics": [ref("Topic")],
                "PolicyDocument": {"Version": "2012-10-17", "Statement": [{
                    "Effect": "Allow", "Principal": {"Service": "cloudwatch.amazonaws.com"},
                    "Action": "sns:Publish", "Resource": ref("Topic"),
                    "Condition": {
                        "StringEquals": {"aws:SourceAccount": ref("AWS::AccountId")},
                        "ArnLike": {"aws:SourceArn": sub("arn:${AWS::Partition}:cloudwatch:${AWS::Region}:${AWS::AccountId}:alarm:${AWS::StackName}-*")},
                    },
                }]},
            },
        },
        "FailureQueue": {"Type": "AWS::SQS::Queue", "Properties": {
            "MessageRetentionPeriod": 1209600, "SqsManagedSseEnabled": True,
        }},
        "Role": {"Type": "AWS::IAM::Role", "Properties": {
            "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole",
            }]},
            "Policies": [{"PolicyName": "DiscordAlertDelivery", "PolicyDocument": {
                "Version": "2012-10-17", "Statement": [
                    {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                     "Resource": sub("arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/lambda/*:*")},
                    {"Effect": "Allow", "Action": "secretsmanager:GetSecretValue", "Resource": ref("WebhookSecretArn")},
                    {"Effect": "Allow", "Action": "sqs:SendMessage", "Resource": arn("FailureQueue")},
                ],
            }}],
        }},
        "Function": {"Type": "AWS::Lambda::Function", "Properties": {
            "Runtime": "python3.13", "Handler": "index.lambda_handler", "Role": arn("Role"),
            "Timeout": 30, "MemorySize": 128,
            "Environment": {"Variables": {
                "WEBHOOK_SECRET_ARN": ref("WebhookSecretArn"), "TOPIC_ARN": ref("Topic"),
                "DISCORD_USER_IDS": ref("DiscordUserIds"),
            }},
            "Code": {"ZipFile": Path(__file__).with_name("lambda_function.py").read_text(encoding="utf-8")},
        }},
        "LogGroup": {"Type": "AWS::Logs::LogGroup", "Properties": {
            "LogGroupName": sub("/aws/lambda/${Function}"), "RetentionInDays": 14,
        }},
        "AsyncDelivery": {"Type": "AWS::Lambda::EventInvokeConfig", "Properties": {
            "FunctionName": ref("Function"), "Qualifier": "$LATEST", "MaximumRetryAttempts": 2,
            "MaximumEventAgeInSeconds": 3600,
            "DestinationConfig": {"OnFailure": {"Destination": arn("FailureQueue")}},
        }},
        "InvokePermission": {"Type": "AWS::Lambda::Permission", "DependsOn": ["LogGroup", "AsyncDelivery"], "Properties": {
            "FunctionName": ref("Function"), "Action": "lambda:InvokeFunction",
            "Principal": "sns.amazonaws.com", "SourceArn": ref("Topic"), "SourceAccount": ref("AWS::AccountId"),
        }},
        "Subscription": {"Type": "AWS::SNS::Subscription", "DependsOn": "InvokePermission", "Properties": {
            "TopicArn": ref("Topic"), "Protocol": "lambda", "Endpoint": arn("Function"),
        }},
    }
    for number, instance in enumerate(instances):
        for suffix, metric, statistic, period, threshold in [
            ("Status", "StatusCheckFailed", "Maximum", 60, 1),
            ("CPU", "CPUUtilization", "Average", 300, ref("CpuThreshold")),
        ]:
            resources[f"Instance{number}{suffix}"] = {
                "Type": "AWS::CloudWatch::Alarm", "DependsOn": ["TopicPolicy", "Subscription"],
                "Properties": {
                    "AlarmName": sub("${AWS::StackName}-" + instance["name"] + "-" + suffix.lower()),
                    "AlarmDescription": f"{instance['name']} 서버의 {metric} 감시",
                    "Namespace": "AWS/EC2", "MetricName": metric, "Statistic": statistic,
                    "Dimensions": [{"Name": "InstanceId", "Value": instance["instance_id"]}],
                    "Period": period, "EvaluationPeriods": 2, "DatapointsToAlarm": 2,
                    "Threshold": threshold, "ComparisonOperator": "GreaterThanOrEqualToThreshold",
                    "TreatMissingData": "missing",
                    "ActionsEnabled": {"Fn::If": ["NotificationsEnabled", True, False]},
                    "AlarmActions": [ref("Topic")], "OKActions": [ref("Topic")],
                },
            }
    template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "EC2 alarms to Discord through SNS and Lambda",
        "Parameters": {
            "WebhookSecretArn": {"Type": "String", "Description": "discord_webhook_url 키를 가진 Secrets Manager 비밀의 전체 ARN",
                                 "AllowedPattern": r"arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:.+"},
            "DiscordUserIds": {"Type": "String", "Default": "", "MaxLength": 1149,
                               "AllowedPattern": r"^$|[0-9]{15,22}(,[0-9]{15,22}){0,49}",
                               "Description": "장애 시 멘션할 Discord 사용자 ID, 쉼표로 구분(최대 50명)"},
            "CpuThreshold": {"Type": "Number", "Default": 80, "MinValue": 1, "MaxValue": 100},
            "EnableNotifications": {"Type": "String", "Default": "false", "AllowedValues": ["true", "false"]},
        },
        "Conditions": {"NotificationsEnabled": {"Fn::Equals": [ref("EnableNotifications"), "true"]}},
        "Resources": resources,
        "Outputs": {
            "TopicArn": {"Value": ref("Topic")}, "FunctionName": {"Value": ref("Function")},
            "FailureQueueUrl": {"Value": ref("FailureQueue")},
        },
    }
    if existing_alarms is not None:
        condition = resources["TopicPolicy"]["Properties"]["PolicyDocument"]["Statement"][0]["Condition"]
        # SNS 정책은 ASCII만 허용하므로 비ASCII 문자를 단일 문자 패턴으로 바꾼다.
        # 정확한 알람 이름은 Lambda에서 다시 검사한다.
        condition["ArnLike"] = {"aws:SourceArn": [
            sub("arn:${AWS::Partition}:cloudwatch:${AWS::Region}:${AWS::AccountId}:alarm:"
                + "".join(char if char.isascii() else "?" for char in name))
            for name in dict.fromkeys(existing_alarms)
        ]}
        resources["Function"]["Properties"]["Environment"]["Variables"]["ALLOWED_ALARM_NAMES"] = json.dumps(existing_alarms, ensure_ascii=True)
        del template["Parameters"]["CpuThreshold"]
        del template["Parameters"]["EnableNotifications"]
        del template["Conditions"]
    # 인스턴스 ID를 지정했을 때만 Spot 예고 규칙을 생성한다.
    template["Parameters"]["SpotInstanceIds"] = {
        "Type": "String", "Default": "",
        "AllowedPattern": r"^$|i-(?:[0-9a-f]{8}|[0-9a-f]{17})(,i-(?:[0-9a-f]{8}|[0-9a-f]{17}))*",
        "Description": "Spot 예고 대상 인스턴스 ID를 쉼표로 구분. 빈 값이면 사용하지 않음",
    }
    template.setdefault("Conditions", {})["SpotEnabled"] = {"Fn::Not": [{"Fn::Equals": [ref("SpotInstanceIds"), ""]}]}
    resources["Function"]["Properties"]["Environment"]["Variables"].update({
        "SPOT_INSTANCE_IDS": ref("SpotInstanceIds"),
        "EXPECTED_ACCOUNT": ref("AWS::AccountId"), "EXPECTED_REGION": ref("AWS::Region"),
    })
    resources["SpotWarningRule"] = {
        "Type": "AWS::Events::Rule", "Condition": "SpotEnabled",
        "DependsOn": ["LogGroup", "AsyncDelivery"],
        "Properties": {
            "Description": "지정된 Spot 인스턴스의 중단 예고를 Discord로 전달",
            "State": "ENABLED",
            "EventPattern": {
                "source": ["aws.ec2"], "detail-type": ["EC2 Spot Instance Interruption Warning"],
                "account": [ref("AWS::AccountId")], "region": [ref("AWS::Region")],
                "detail": {"instance-id": {"Fn::Split": [",", ref("SpotInstanceIds")]},
                           "instance-action": ["stop", "terminate", "hibernate"]},
            },
            "Targets": [{"Id": "DiscordNotifier", "Arn": arn("Function"),
                         "RetryPolicy": {"MaximumEventAgeInSeconds": 300, "MaximumRetryAttempts": 2}}],
        },
    }
    resources["SpotInvokePermission"] = {
        "Type": "AWS::Lambda::Permission", "Condition": "SpotEnabled",
        "Properties": {"FunctionName": ref("Function"), "Action": "lambda:InvokeFunction",
                       "Principal": "events.amazonaws.com", "SourceArn": arn("SpotWarningRule"),
                       "SourceAccount": ref("AWS::AccountId")},
    }
    return template


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instances", type=Path)
    parser.add_argument("--existing-alarms", action="store_true", help="입력 파일을 기존 알람 이름 목록으로 처리한다")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "generated" / "discord-alerts.template.json")
    args = parser.parse_args()
    values = json.loads(args.instances.read_text(encoding="utf-8-sig"))
    template = build([], existing_alarms=values) if args.existing_alarms else build(values)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"템플릿 생성 완료: {args.output}")
