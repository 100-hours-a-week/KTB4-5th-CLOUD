"""SLI-SLO-design.md 기준의 CloudWatch 로그 그룹·지표 필터·알람·대시보드 템플릿을 만든다.

알람 알림은 기존 Discord 알림 스택(damuckja-v1-discord-alerts)의 SNS 토픽으로 보낸다.
사용법: python build_template.py [--env dev|prod] [--print-alarm-names]
"""

import argparse
import json
import re
from pathlib import Path

SLO_TARGET = 95.0                      # SLI-SLO-design.md 4장: 작업 성공률 월간 95%
ERROR_BUDGET = 100 - SLO_TARGET        # 5%

# nginx map($slo_route)의 값, 지표 이름 접두어, 문서용 이름
ROUTES = [
    ("ingredient_create", "IngredientCreate", "수동 재고 등록"),
    ("ingredient_read", "IngredientRead", "재고 조회"),
    ("notification_poll", "NotificationPoll", "알림 조회(폴링)"),
]

# 다중 기간 소진율 알람(Google SRE Workbook, Alerting on SLOs 6번 방식).
# 긴 기간과 짧은 기간이 모두 기준을 넘을 때만 알린다. min_*는 저트래픽 오탐 방지용 최소 평가 건수.
BURN_RULES = [
    {"name": "fast-burn", "severity": "page", "burn": 14.4,
     "long": 3600, "short": 300, "min_long": 10, "min_short": 3},
    {"name": "slow-burn", "severity": "ticket", "burn": 6.0,
     "long": 21600, "short": 1800, "min_long": 20, "min_short": 5},
]

ENVIRONMENTS = {
    # dev는 SLO 평가 대상이 아니므로 작업별 지표·소진율 알람 없이 오류 급증만 본다.
    "dev": {"slo": False},
    "prod": {"slo": True},
}


def ref(name):
    return {"Ref": name}


def dashed(route):
    return route.replace("_", "-")


def namespace(env):
    return f"Dameokja/{env}"


# 로그 그룹은 CloudWatch Agent가 만들고 보관 기간은 콘솔에서 관리한다. 템플릿은 이름만 참조한다.
def log_group(env, kind):
    return f"/dameokja/{env}/{kind}"


def metric_filter(env, metric, pattern, group="nginx-access"):
    return {
        "Type": "AWS::Logs::MetricFilter",
        "Properties": {
            "LogGroupName": log_group(env, group),
            "FilterName": f"dameokja-{env}-{metric}",
            "FilterPattern": pattern,
            "MetricTransformations": [{
                "MetricNamespace": namespace(env), "MetricName": metric,
                "MetricValue": "1", "DefaultValue": 0, "Unit": "Count",
            }],
        },
    }


def error_rate_expression(minimum):
    # 4xx는 평가 제외: 분모는 2xx + 5xx만 사용한다. 최소 건수 미만이면 0으로 둔다.
    valid = "(FILL(g,0)+FILL(b,0))"
    return f"IF({valid} >= {minimum}, 100*FILL(b,0)/{valid}, 0)"


def burn_child(env, prefix, label, period, minimum, threshold):
    return {
        "Type": "AWS::CloudWatch::Alarm",
        "Properties": {
            "AlarmName": f"dameokja-{env}-slo-{dashed(label[0])}-{label[1]}",
            "AlarmDescription": f"{label[2]} 오류율(4xx 제외) {period // 60}분 창. 단독으로는 알리지 않고 복합 알람에서만 사용",
            "ActionsEnabled": False,
            "Metrics": [
                {"Id": "g", "ReturnData": False, "MetricStat": {
                    "Metric": {"Namespace": namespace(env), "MetricName": f"{prefix}Good"},
                    "Period": period, "Stat": "Sum"}},
                {"Id": "b", "ReturnData": False, "MetricStat": {
                    "Metric": {"Namespace": namespace(env), "MetricName": f"{prefix}Bad"},
                    "Period": period, "Stat": "Sum"}},
                {"Id": "e", "ReturnData": True, "Label": "오류율(%)",
                 "Expression": error_rate_expression(minimum)},
            ],
            "EvaluationPeriods": 1, "DatapointsToAlarm": 1,
            "Threshold": threshold, "ComparisonOperator": "GreaterThanOrEqualToThreshold",
            "TreatMissingData": "notBreaching",
        },
    }


def simple_alarm(name, description, metric, period, threshold, evaluation=1):
    return {
        "Type": "AWS::CloudWatch::Alarm",
        "Properties": {
            "AlarmName": name, "AlarmDescription": description,
            "ActionsEnabled": True, "AlarmActions": [ref("AlarmTopicArn")], "OKActions": [ref("AlarmTopicArn")],
            **metric, "Period": period,
            "EvaluationPeriods": evaluation, "DatapointsToAlarm": evaluation,
            "Threshold": threshold, "ComparisonOperator": "GreaterThanOrEqualToThreshold",
            "TreatMissingData": "notBreaching",
        },
    }


def build(env):
    if env not in ENVIRONMENTS:
        raise ValueError("env는 dev 또는 prod여야 합니다")
    options = ENVIRONMENTS[env]
    resources = {
        "Http5xxFilter": metric_filter(env, "Http5xx", "{ $.status >= 500 }"),
        "Http4xxFilter": metric_filter(env, "Http4xx", "{ $.status >= 400 && $.status < 500 }"),
        # Spring 로그 형식: "<시각>  ERROR <pid> --- [app] ..." 에서 레벨 자리만 맞춘다.
        "BackendErrorFilter": metric_filter(env, "BackendError", "%ERROR [0-9]+ --- %", group="backend"),
    }
    notified = []

    def add_alarm(key, alarm):
        resources[key] = alarm
        notified.append(alarm["Properties"]["AlarmName"])

    if options["slo"]:
        for route, prefix, title in ROUTES:
            resources[f"{prefix}GoodFilter"] = metric_filter(
                env, f"{prefix}Good", f'{{ $.route = "{route}" && $.status >= 200 && $.status < 300 }}')
            resources[f"{prefix}BadFilter"] = metric_filter(
                env, f"{prefix}Bad", f'{{ $.route = "{route}" && $.status >= 500 }}')
            for rule in BURN_RULES:
                threshold = round(rule["burn"] * ERROR_BUDGET, 2)
                children = []
                for window in ("long", "short"):
                    period = rule[window]
                    label = (route, f"{rule['name']}-{period // 60}m", title)
                    key = f"{prefix}{rule['name'].title().replace('-', '')}{window.title()}"
                    resources[key] = burn_child(env, prefix, label, period, rule[f"min_{window}"], threshold)
                    children.append(key)
                name = f"dameokja-{env}-slo-{dashed(route)}-{rule['name']}-{rule['severity']}"
                rule_text = " AND ".join(
                    f'ALARM("{resources[child]["Properties"]["AlarmName"]}")' for child in children)
                add_alarm(f"{prefix}{rule['name'].title().replace('-', '')}Composite", {
                    "Type": "AWS::CloudWatch::CompositeAlarm",
                    "DependsOn": children,
                    "Properties": {
                        "AlarmName": name,
                        "AlarmDescription": (
                            f"{title} 오류 예산 소진율 {rule['burn']}배 이상(오류율 {threshold}% 이상)이 "
                            f"{rule['long'] // 60}분·{rule['short'] // 60}분 창에서 동시에 발생. "
                            "대응: Discord 알림 → 대시보드 → nginx/BE 로그 순서로 확인"),
                        "AlarmRule": rule_text,
                        "ActionsEnabled": True,
                        "AlarmActions": [ref("AlarmTopicArn")], "OKActions": [ref("AlarmTopicArn")],
                    },
                })

    add_alarm("Http5xxAlarm", simple_alarm(
        f"dameokja-{env}-http-5xx-ticket", "전체 API 5xx가 5분간 기준 건수 이상(SLI 비대상 API 포함 일반 오류 감시)",
        {"Namespace": namespace(env), "MetricName": "Http5xx", "Statistic": "Sum"}, 300, ref("Http5xxThreshold")))
    if options["slo"]:
        add_alarm("Http4xxAlarm", simple_alarm(
            f"dameokja-{env}-http-4xx-surge-ticket",
            "4xx가 1시간 기준 건수 이상. SLO에서 제외되는 4xx 중 서버 버그·인증 장애로 인한 급증 확인용",
            {"Namespace": namespace(env), "MetricName": "Http4xx", "Statistic": "Sum"}, 3600, ref("Http4xxHourlyThreshold")))
    add_alarm("MemoryAlarm", simple_alarm(
        f"dameokja-{env}-memory-ticket", "앱 서버 메모리 사용률 90% 이상 10분 지속(CloudWatch Agent)",
        {"Namespace": "CWAgent", "MetricName": "mem_used_percent", "Statistic": "Average",
         "Dimensions": [{"Name": "InstanceId", "Value": ref("InstanceId")}]}, 300, 90, evaluation=2))
    add_alarm("DiskAlarm", simple_alarm(
        f"dameokja-{env}-disk-ticket", "앱 서버 루트 디스크 사용률 85% 이상(로그 증가 감시)",
        {"Namespace": "CWAgent", "MetricName": "disk_used_percent", "Statistic": "Average",
         "Dimensions": [{"Name": "InstanceId", "Value": ref("InstanceId")}, {"Name": "path", "Value": "/"},
                        {"Name": "fstype", "Value": ref("RootFsType")}]}, 300, 85))

    resources.update(query_definitions(env))
    if options["slo"]:
        resources["Dashboard"] = dashboard(env, notified)
    resources["InfraDashboard"] = infra_dashboard(env)

    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": f"Dameokja {env} SLI log metrics, SLO burn-rate alarms and dashboard",
        "Parameters": {
            "AlarmTopicArn": {"Type": "String", "AllowedPattern": r"arn:aws:sns:[a-z0-9-]+:[0-9]{12}:.+",
                              "Description": "damuckja-v1-discord-alerts 스택 Outputs의 TopicArn"},
            "InstanceId": {"Type": "String", "AllowedPattern": r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})",
                           "Description": "CloudWatch Agent가 설치된 앱 서버 인스턴스 ID"},
            "RootFsType": {"Type": "String", "Default": "ext4",
                           "Description": "Agent disk_used_percent의 fstype 차원 값(콘솔 지표에서 확인)"},
            "Http5xxThreshold": {"Type": "Number", "Default": 5, "MinValue": 1},
            **({"DiscordFunctionName": {"Type": "String", "Description": "Discord 알림 스택 Outputs의 FunctionName(대시보드용)"},
                "DiscordFailureQueueName": {"Type": "String", "Description": "Discord 알림 스택 Outputs의 FailureQueueUrl 마지막 경로(큐 이름)"}}
               if options["slo"] else {}),
            **({"Http4xxHourlyThreshold": {"Type": "Number", "Default": 100, "MinValue": 1,
                                           "Description": "운영 2~4주 후 평소 4xx 건수를 보고 조정"}}
               if options["slo"] else {}),
        },
        "Resources": resources,
        "Outputs": {"NotifiedAlarmNames": {"Value": ",".join(notified)}},
    }


def query_definitions(env):
    nginx, backend = log_group(env, "nginx-access"), log_group(env, "backend")
    queries = {
        "SloMonthlyQuery": ("SLO 작업별 응답 코드 건수", [nginx],
            'filter route != "-"\n'
            '| fields floor(status / 100) as class\n'
            '| stats count(*) as requests by route, class\n'
            '| sort route, class'),
        "Status4xxQuery": ("4xx 코드별 건수", [nginx],
            'filter status >= 400 and status < 500\n| stats count(*) as requests by status, route\n| sort requests desc'),
        "LatencyQuery": ("API 서버 처리시간 p95·p99(보조, nginx request_time)", [nginx],
            'filter route != "-" and (status < 400 or status >= 500)\n'
            '| stats count(*) as requests, pct(request_time, 95) as p95_sec, pct(request_time, 99) as p99_sec by route'),
        "MorningBatchQuery": ("아침 알림 배치 결과", [backend],
            'filter @message like /알림 생성 단계를 마쳤습니다/\n'
            '| parse @message "refrigeratorCount=*, failedRefrigeratorCount=*, elapsedMillis=*" as total, failed, elapsed\n'
            '| fields @timestamp, total, failed, elapsed\n| sort @timestamp desc'),
        "PushFailureQuery": ("Push 발송 실패 로그", [backend],
            'filter @message like /푸시를 전송하지 못했습니다|Web Push 전송에 실패했습니다|푸시 발송 작업을 처리하지 못했습니다/\n'
            '| stats count(*) as failures by bin(1h)'),
    }
    return {key: {"Type": "AWS::Logs::QueryDefinition",
                  "Properties": {"Name": f"dameokja-{env}/{name}", "LogGroupNames": groups, "QueryString": query}}
            for key, (name, groups, query) in queries.items()}


def metric_widget(title, metrics, x, y, stat="Average", period=300, width=8, height=6, line=None, percent=False, region=None):
    props = {"title": title, "view": "timeSeries", "region": region or "${AWS::Region}",
             "period": period, "stat": stat, "metrics": metrics}
    if percent:
        props["yAxis"] = {"left": {"min": 0, "max": 100}}
    if line is not None:
        props["annotations"] = {"horizontal": [{"label": "기준선", "value": line}]}
    return {"type": "metric", "x": x, "y": y, "width": width, "height": height, "properties": props}


def text_widget(markdown, y, height=1):
    return {"type": "text", "x": 0, "y": y, "width": 24, "height": height, "properties": {"markdown": markdown}}


def ec2(name, label=None):
    return ["AWS/EC2", name, "InstanceId", "${InstanceId}", {"label": label or name}]


def agent_search(name, label):
    # 차원 구성(interface·exe 등)이 서버마다 달라도 표시되도록 검색식으로 찾는다.
    # {CWAgent}처럼 스키마를 쓰면 '차원이 없는 지표'만 찾으므로 Namespace= 조건으로 검색한다.
    return [[{"expression": f"SEARCH('Namespace=\"CWAgent\" MetricName=\"{name}\" InstanceId=\"${{InstanceId}}\"', 'Average', 60)",
             "label": label, "id": "s" + re.sub(r"[^a-z0-9]", "", name)}]]


def infra_widgets(env, y):
    """앱 서버 인스턴스 자원. ${InstanceId}·${RootFsType} 등은 스택 파라미터로 치환된다."""
    w = [text_widget("### EC2 기본 지표 (무료, 5분 주기)", y)]
    y += 1
    w += [
        metric_widget("CPU 사용률(%)", [ec2("CPUUtilization", "CPU")], 0, y, line=80, percent=True),
        metric_widget("CPU 크레딧", [ec2("CPUCreditBalance", "잔량"), ec2("CPUCreditUsage", "사용량")], 8, y),
        metric_widget("CPU 초과 크레딧(무제한 모드 과금)", [ec2("CPUSurplusCreditBalance", "초과 잔량"),
                                                     ec2("CPUSurplusCreditsCharged", "과금된 크레딧")], 16, y),
    ]
    y += 6
    w += [
        metric_widget("네트워크 바이트", [ec2("NetworkIn", "In"), ec2("NetworkOut", "Out")], 0, y, stat="Sum"),
        metric_widget("네트워크 패킷", [ec2("NetworkPacketsIn", "In"), ec2("NetworkPacketsOut", "Out")], 8, y, stat="Sum"),
        metric_widget("상태 검사 실패(1=실패)", [ec2("StatusCheckFailed_Instance", "인스턴스"),
                                           ec2("StatusCheckFailed_System", "시스템"),
                                           ec2("StatusCheckFailed_AttachedEBS", "EBS")], 16, y, stat="Maximum"),
    ]
    y += 6
    w += [
        metric_widget("EBS 읽기·쓰기 횟수", [ec2("EBSReadOps", "읽기"), ec2("EBSWriteOps", "쓰기")], 0, y, stat="Sum"),
        metric_widget("EBS 읽기·쓰기 바이트", [ec2("EBSReadBytes", "읽기"), ec2("EBSWriteBytes", "쓰기")], 8, y, stat="Sum"),
        metric_widget("EBS 버스트 잔량(%)", [ec2("EBSIOBalance%", "I/O"), ec2("EBSByteBalance%", "처리량")], 16, y, percent=True),
    ]
    y += 6
    w += [text_widget("### 서버 OS 지표 (CloudWatch Agent, 1분 주기)", y)]
    y += 1
    w += [
        metric_widget("메모리 사용률(%)", [["CWAgent", "mem_used_percent", "InstanceId", "${InstanceId}", {"label": "메모리"}]],
                      0, y, period=60, line=90, percent=True),
        metric_widget("디스크 사용률(%) — 루트 /", [["CWAgent", "disk_used_percent", "InstanceId", "${InstanceId}",
                                                "path", "/", "fstype", "${RootFsType}", {"label": "디스크"}]],
                      8, y, period=60, line=85, percent=True),
        metric_widget("프로세스 상태", agent_search("processes_running", "실행 중") + agent_search("processes_blocked", "대기(blocked)"),
                      16, y, period=60),
    ]
    y += 6
    w += [
        metric_widget("TCP 연결", agent_search("netstat_tcp_established", "ESTABLISHED") +
                      agent_search("netstat_tcp_time_wait", "TIME_WAIT"), 0, y, period=60),
        metric_widget("네트워크 오류", agent_search("net_err_in", "수신 오류") + agent_search("net_err_out", "송신 오류"),
                      8, y, period=60),
        metric_widget("네트워크 드롭", agent_search("net_drop_in", "수신 드롭") + agent_search("net_drop_out", "송신 드롭"),
                      16, y, period=60),
    ]
    y += 6
    w += [
        metric_widget("프로세스별 CPU(%) — java·nginx·node", agent_search("procstat_cpu_usage", ""), 0, y, period=60, width=12),
        metric_widget("프로세스별 메모리(RSS, 바이트)", agent_search("procstat_memory_rss", ""), 12, y, period=60, width=12),
    ]
    y += 6
    w += [text_widget("### BE 애플리케이션 (Actuator → Prometheus → Agent, 1분 주기)", y)]
    y += 1
    app = f"Dameokja/{env}/App"

    def app_metric(name, label, dims=None):
        return [app, name, "env", env, *(dims or []), {"label": label}]
    w += [
        metric_widget("Tomcat 요청 스레드", [app_metric("tomcat_threads_busy_threads", "사용 중"),
                                         app_metric("tomcat_threads_current_threads", "생성됨"),
                                         app_metric("tomcat_threads_config_max_threads", "최대")], 0, y, period=60),
        metric_widget("DB 커넥션 풀(HikariCP)", [app_metric("hikaricp_connections_active", "사용 중"),
                                              app_metric("hikaricp_connections_idle", "유휴"),
                                              app_metric("hikaricp_connections_pending", "대기"),
                                              app_metric("hikaricp_connections_max", "최대")], 8, y, period=60),
        metric_widget("DB 커넥션 획득 타임아웃", [app_metric("hikaricp_connections_timeout_total", "타임아웃")],
                      16, y, stat="Sum", period=60),
    ]
    y += 6
    sli = [("POST", "/api/v1/refrigerators/{refrigeratorId}/ingredients", "재고 등록"),
           ("GET", "/api/v1/refrigerators/{refrigeratorId}/ingredients", "재고 목록"),
           ("GET", "/api/v1/ingredients/{ingredientId}", "재고 상세"),
           ("GET", "/api/v1/refrigerators/{refrigeratorId}/notifications", "알림 목록"),
           ("GET", "/api/v1/refrigerators/{refrigeratorId}/notifications/stream", "알림 최신"),
           ("GET", "/api/v1/refrigerators/{refrigeratorId}/notifications/unread-count", "미읽음 수")]
    counts, latency = [], []
    for i, (method, uri, label) in enumerate(sli):
        dims = ["method", method, "uri", uri]
        counts.append(app_metric("http_server_requests_seconds_count", label, dims))
        latency += [[app, "http_server_requests_seconds_sum", "env", env, *dims, {"id": f"s{i}", "visible": False}],
                    [app, "http_server_requests_seconds_count", "env", env, *dims, {"id": f"c{i}", "visible": False}],
                    [{"expression": f"1000*s{i}/c{i}", "label": label, "id": f"l{i}"}]]
    w += [
        metric_widget("JVM 스레드", [app_metric("jvm_threads_live_threads", "전체"),
                                   app_metric("jvm_threads_daemon_threads", "데몬"),
                                   app_metric("jvm_threads_peak_threads", "최대 기록")], 0, y, period=60),
        metric_widget("JVM 스레드 상태별", [[{"expression": f"SEARCH('{{{app},env,state}} MetricName=\"jvm_threads_states_threads\" env=\"{env}\"', 'Average', 60)",
                                         "label": "", "id": "st"}]], 8, y, period=60),
        metric_widget("SLI API 요청 수(BE 기준)", counts, 16, y, stat="Sum", period=60),
    ]
    y += 6
    w += [metric_widget("SLI API 평균 처리 시간(ms, BE 기준)", latency, 0, y, stat="Sum", period=300, width=24)]
    y += 6
    w += [text_widget("### 애플리케이션 로그", y)]
    y += 1
    w += [
        metric_widget("BE ERROR 로그 건수", [[namespace(env), "BackendError", {"label": "ERROR"}]], 0, y, stat="Sum", width=12),
        metric_widget("CloudWatch 로그 수신량(바이트) — 수집 중단 확인", [
            ["AWS/Logs", "IncomingBytes", "LogGroupName", log_group(env, "backend"), {"label": "backend"}],
            ["AWS/Logs", "IncomingBytes", "LogGroupName", log_group(env, "nginx-access"), {"label": "nginx"}]],
            12, y, stat="Sum", width=12),
    ]
    y += 6
    return w, y


def account_widgets(y):
    """계정 공용 리소스(prod 대시보드에만 표시)."""
    w = [text_widget("### 알림 경로·비용 (계정 공용)", y)]
    y += 1
    w += [
        metric_widget("Discord 알림 Lambda", [
            ["AWS/Lambda", "Invocations", "FunctionName", "${DiscordFunctionName}", {"label": "호출"}],
            ["AWS/Lambda", "Errors", "FunctionName", "${DiscordFunctionName}", {"label": "오류"}]], 0, y, stat="Sum"),
        metric_widget("Discord 알림 실패 큐(쌓이면 전송 실패)", [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", "${DiscordFailureQueueName}", {"label": "메시지 수"}]],
            8, y, stat="Maximum"),
        metric_widget("예상 청구 금액(USD, 월 누적)", [
            ["AWS/Billing", "EstimatedCharges", "Currency", "USD", {"label": "전체"}]],
            16, y, stat="Maximum", period=21600, region="us-east-1"),
    ]
    y += 6
    return w, y


def infra_dashboard(env):
    widgets = [text_widget(
        f"## 다먹자 {env} 앱 서버 자원 (인스턴스 ${{InstanceId}})\n"
        "가로선은 알람 기준(메모리 90%, 디스크 85%)이며 CPU 80%는 참고용입니다.", 0, 2)]
    more, y = infra_widgets(env, 2)
    widgets += more
    if ENVIRONMENTS[env]["slo"]:
        more, y = account_widgets(y)
        widgets += more
    body = json.dumps({"widgets": widgets}, ensure_ascii=False)
    return {"Type": "AWS::CloudWatch::Dashboard", "Properties": {
        "DashboardName": f"dameokja-{env}-infra", "DashboardBody": {"Fn::Sub": body}}}


def dashboard(env, alarm_names):
    ns = namespace(env)
    widgets = [{"type": "text", "x": 0, "y": 0, "width": 24, "height": 3, "properties": {"markdown":
        f"## 다먹자 {env} SLO (목표 {SLO_TARGET:g}%, 4xx 제외)\n"
        "상단 시간 범위를 **KST 기준 해당 월 1일 00:00 ~ 다음 달 1일 00:00**(절대 시간, 시간대 +09:00)으로 두면 "
        "숫자 위젯이 월간 SLO 판정값이 됩니다. 응답시간 SLO는 Sentry에서 확인합니다."}}]
    y = 3
    for index, (route, prefix, title) in enumerate(ROUTES):
        good = [ns, f"{prefix}Good", {"id": "g", "stat": "Sum", "visible": False}]
        bad = [ns, f"{prefix}Bad", {"id": "b", "stat": "Sum", "visible": False}]
        widgets.append({"type": "metric", "x": index * 8, "y": y, "width": 8, "height": 4, "properties": {
            "title": f"{title}: 선택 기간 성공률·오류 예산", "view": "singleValue", "region": "${AWS::Region}",
            "setPeriodToTimeRange": True, "stat": "Sum",
            "metrics": [good, bad,
                        [{"id": "rate", "label": "성공률(%)", "expression": "100*FILL(g,0)/(FILL(g,0)+FILL(b,0))"}],
                        [{"id": "budget", "label": "남은 오류 예산(%)",
                          "expression": f"100-100*FILL(b,0)/((FILL(g,0)+FILL(b,0))*{ERROR_BUDGET / 100:g})"}],
                        [{"id": "valid", "label": "평가 대상 건수", "expression": "FILL(g,0)+FILL(b,0)"}]]}})
    y += 4
    widgets.append({"type": "metric", "x": 0, "y": y, "width": 12, "height": 6, "properties": {
        "title": "작업별 1시간 오류율(%)", "view": "timeSeries", "region": "${AWS::Region}", "period": 3600, "stat": "Sum",
        "annotations": {"horizontal": [{"label": "fast-burn", "value": 14.4 * ERROR_BUDGET},
                                       {"label": "slow-burn", "value": 6 * ERROR_BUDGET}]},
        "metrics": [m for i, (route, prefix, title) in enumerate(ROUTES) for m in (
            [ns, f"{prefix}Good", {"id": f"g{i}", "visible": False}],
            [ns, f"{prefix}Bad", {"id": f"b{i}", "visible": False}],
            [{"id": f"e{i}", "label": title,
              "expression": f"100*FILL(b{i},0)/(FILL(g{i},0)+FILL(b{i},0))"}])]}})
    widgets.append({"type": "metric", "x": 12, "y": y, "width": 12, "height": 6, "properties": {
        "title": "전체 API 4xx·5xx 건수(5분)", "view": "timeSeries", "region": "${AWS::Region}", "period": 300, "stat": "Sum",
        "metrics": [[ns, "Http5xx", {"label": "5xx"}], [ns, "Http4xx", {"label": "4xx"}]]}})
    y += 6
    widgets.append({"type": "alarm", "x": 0, "y": y, "width": 24, "height": 3, "properties": {
        "title": "알람 상태", "alarms": [f"arn:aws:cloudwatch:${{AWS::Region}}:${{AWS::AccountId}}:alarm:{name}"
                                       for name in alarm_names]}})
    body = json.dumps({"widgets": widgets}, ensure_ascii=False)
    return {"Type": "AWS::CloudWatch::Dashboard", "Properties": {
        "DashboardName": f"dameokja-{env}-slo", "DashboardBody": {"Fn::Sub": body}}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", choices=sorted(ENVIRONMENTS), action="append")
    parser.add_argument("--print-alarm-names", action="store_true",
                        help="Discord 알림 스택 existing-alarms.json에 넣을 알람 이름만 출력")
    args = parser.parse_args()
    out_dir = Path(__file__).resolve().parent / "generated"
    for env in args.env or sorted(ENVIRONMENTS):
        template = build(env)
        if args.print_alarm_names:
            print("\n".join(template["Outputs"]["NotifiedAlarmNames"]["Value"].split(",")))
            continue
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"slo-monitoring.{env}.template.json"
        path.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"템플릿 생성 완료: {path}")
