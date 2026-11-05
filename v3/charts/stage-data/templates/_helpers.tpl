{{- define "stage-data.redisConfig" -}}
bind 0.0.0.0
protected-mode yes
port 6379
dir /data
appendonly yes
appendfsync everysec
save ""
maxmemory 512mb
{{- end -}}


{{/* stage 앱 Node(stage-app NodePool)에만 둡니다. */}}
{{- define "stage-data.stageNode" -}}
nodeSelector:
  dameokja.com/environment: stage
tolerations:
  - {key: dameokja.com/environment, operator: Equal, value: stage, effect: NoSchedule}
{{- end -}}
