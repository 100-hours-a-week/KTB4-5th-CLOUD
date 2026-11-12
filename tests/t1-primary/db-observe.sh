#!/bin/sh
# stage DB의 관측 전용 프로세스이며 최대 네 시간 후 스스로 종료한다.
set -eu
mkdir -p /var/tmp/t1-issue329
cat > /var/tmp/t1-issue329/observe.py <<'PY'
import datetime,json,os,subprocess,time
from pathlib import Path
start=time.monotonic()
with open('/var/tmp/t1-issue329/db-observations.jsonl','a',buffering=1) as out:
    while time.monotonic()-start < 14400:
        row={'time':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        for name in ['stat','meminfo','diskstats','net/dev']:
            row[name]=Path('/proc/'+name).read_text()
        result=subprocess.run(['mysql','-uroot','--batch','--skip-column-names','-e',
            "SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Threads_running','Queries','Slow_queries','Innodb_buffer_pool_reads','Innodb_buffer_pool_read_requests','Innodb_data_reads','Innodb_data_writes','Innodb_row_lock_waits','Innodb_row_lock_time')"],capture_output=True,text=True,timeout=10)
        row['mysql']=result.stdout
        row['mysql_exit']=result.returncode
        out.write(json.dumps(row)+'\n')
        time.sleep(15)
PY
MYSQL_PWD=$(aws ssm get-parameter --name /dameokja-v3-stage/MYSQL_ROOT_PASSWORD --with-decryption --region ap-northeast-2 --query Parameter.Value --output text)
export MYSQL_PWD
nohup python3 /var/tmp/t1-issue329/observe.py > /var/tmp/t1-issue329/observe.log 2>&1 < /dev/null &
echo $! > /var/tmp/t1-issue329/observe.pid
unset MYSQL_PWD
echo 'stage DB 관측 시작'
