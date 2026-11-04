{{/* 외부 HTTPS 대상. 사설 대역을 빼서 VPC 안 다른 서비스로 가는 443은 막습니다. S3는 Gateway Endpoint를 타지만 공인 주소라 여기에 포함됩니다. */}}
{{- define "platform.internet" -}}
{cidr: 0.0.0.0/0, except: [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16]}
{{- end }}

{{/* Gateway ALB가 놓인 Subnet. ALB 주소는 바뀔 수 있어 Subnet 단위로 엽니다. */}}
{{- define "platform.albSources" -}}
{{- range .Values.network.albSubnets }}
- ipBlock: {cidr: {{ .cidr }}}
{{- end }}
{{- end }}
