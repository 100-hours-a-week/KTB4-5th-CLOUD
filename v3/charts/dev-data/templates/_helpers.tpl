{{- define "dev-data.redisConfig" -}}
bind 0.0.0.0
protected-mode yes
port 6379
dir /data
appendonly yes
appendfsync everysec
save ""
maxmemory 512mb
{{- end -}}
