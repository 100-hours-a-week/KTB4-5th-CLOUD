{{- define "prod-data.redisConfig" -}}
bind 0.0.0.0
protected-mode yes
port 6379
dir /data
appendonly yes
appendfsync everysec
save ""
maxmemory 512mb
{{- end -}}


{{/* prod 앱 Node(prod-app NodePool)에만 둡니다. */}}
{{- define "prod-data.prodNode" -}}
nodeSelector:
  dameokja.com/environment: prod
tolerations:
  - {key: dameokja.com/environment, operator: Equal, value: prod, effect: NoSchedule}
{{- end -}}
