#!/bin/bash
# 로컬 리허설용 계정. 비밀번호는 compose 실행 시 환경변수로만 전달
set -e
mysql -uroot -p"$MYSQL_ROOT_PASSWORD" <<SQL
CREATE USER 'migadmin'@'%' IDENTIFIED BY '${MIG_PASSWORD}';
GRANT ALL PRIVILEGES ON *.* TO 'migadmin'@'%';
CREATE USER 'repl'@'%' IDENTIFIED BY '${REPL_PASSWORD}';
GRANT REPLICATION SLAVE ON *.* TO 'repl'@'%';
SQL
