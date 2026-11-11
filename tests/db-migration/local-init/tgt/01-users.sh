#!/bin/bash
# 신규 DB 관리 계정만 만들고, 이후 재시작에도 유지되도록 super_read_only를 켬 (복제 적용 스레드만 쓸 수 있음)
set -e
mysql -uroot -p"$MYSQL_ROOT_PASSWORD" <<SQL
CREATE USER 'migadmin'@'%' IDENTIFIED BY '${MIG_PASSWORD}';
GRANT ALL PRIVILEGES ON *.* TO 'migadmin'@'%';
SET PERSIST super_read_only = ON;
SQL
