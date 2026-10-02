#!/usr/bin/env bash
# EC2 한 대에 SLI 로그 수집을 준비한다. 서버마다 최초 1회, Agent 설정을 바꿀 때마다 다시 실행한다.
# 사용법: sudo bash setup-host.sh <dev|prod>
set -Eeuo pipefail

env_name="${1:?dev 또는 prod 필요}"
[[ "$env_name" == dev || "$env_name" == prod ]] || { echo "dev 또는 prod만 허용합니다."; exit 2; }
[[ $EUID -eq 0 ]] || { echo "root 권한으로 실행해야 합니다."; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
agent_config="$here/../cloudwatch-agent/amazon-cloudwatch-agent.$env_name.json"
test -f "$agent_config"

# 1) 로그 디렉터리. BE 컨테이너는 uid 10001(app)로 실행되므로 소유자를 맞춘다.
#    Docker가 먼저 만들면 root 소유가 되어 BE가 파일 로그를 쓰지 못한다.
install -d -m 0755 /var/log/dameokja /var/log/dameokja/nginx
install -d -m 0755 -o 10001 -g 10001 /var/log/dameokja

# 2) nginx 로그 회전(BE 로그는 Spring Boot가 직접 회전한다).
install -m 0644 "$here/logrotate-dameokja-nginx" /etc/logrotate.d/dameokja-nginx
logrotate --debug /etc/logrotate.d/dameokja-nginx >/dev/null

# 3) CloudWatch Agent 설치 확인 후 설정 적용.
ctl=/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl
if [[ ! -x "$ctl" ]]; then
    arch=$(dpkg --print-architecture)   # t4g는 arm64
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSL -o "$tmp/agent.deb" \
        "https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/$arch/latest/amazon-cloudwatch-agent.deb"
    dpkg -i "$tmp/agent.deb"
fi
install -m 0644 "$agent_config" /opt/aws/amazon-cloudwatch-agent/etc/dameokja.json
install -m 0644 "$here/../cloudwatch-agent/prometheus.$env_name.yaml" /opt/aws/amazon-cloudwatch-agent/etc/prometheus.yaml
"$ctl" -a fetch-config -m ec2 -s -c file:/opt/aws/amazon-cloudwatch-agent/etc/dameokja.json
"$ctl" -a status
echo "완료: nginx·BE 컨테이너를 다시 만들면(docker compose up -d) 파일 로그가 생성됩니다."
