#!/usr/bin/env bash
# 원본 → 신규 MySQL 이전 절차: 사전 확인, mysqldump 초기 복사, GTID 복제, 스키마 비교, pt-table-checksum
# 원본·신규 양쪽에 접속할 수 있는 곳(신규 DB 서버 등)에서 실행한다. 전환 자체는 migtest.py run이 수행한다.
#
# 필요한 환경변수 (비밀번호는 출력하지 않음)
#   SRC_HOST SRC_PORT SRC_USER SRC_PASSWORD   원본 관리 계정
#   TGT_HOST TGT_PORT TGT_USER TGT_PASSWORD   신규 관리 계정
#   REPL_USER REPL_PASSWORD                   원본의 복제 전용 계정 (REPLICATION SLAVE)
#   DB_NAME(기본 dameokja) WORK_DIR(기본 ./work) PT_CMD(기본 pt-table-checksum)
set -euo pipefail

DB_NAME=${DB_NAME:-dameokja}
WORK_DIR=${WORK_DIR:-./work}
PT_CMD=${PT_CMD:-pt-table-checksum}
mkdir -p "$WORK_DIR"
WORK_DIR=$(cd "$WORK_DIR" && pwd)  # pt-table-checksum DSN의 F=에 절대 경로가 필요
umask 077

# 비밀번호를 명령줄에 남기지 않도록 접속 정보 파일을 임시로 만든다
cnf() {
  local p=$1 f="$WORK_DIR/.$1.cnf" host port user pass
  host=${p}_HOST port=${p}_PORT user=${p}_USER pass=${p}_PASSWORD
  printf '[client]\nhost=%s\nport=%s\nuser=%s\npassword=%s\n' \
    "${!host}" "${!port:-3306}" "${!user}" "${!pass}" > "$f"
  echo "$f"
}
SRC_CNF=$(cnf SRC)
TGT_CNF=$(cnf TGT)
trap 'rm -f "$SRC_CNF" "$TGT_CNF"' EXIT
src() { mysql --defaults-extra-file="$SRC_CNF" -N -B "$@"; }
tgt() { mysql --defaults-extra-file="$TGT_CNF" -N -B "$@"; }
ts() { date '+%Y-%m-%dT%H:%M:%S.%3N%z'; }
log() { echo "[$(ts)] $*"; }
el() { awk -v a="$1" -v b="$2" 'BEGIN{printf "%.1f", b-a}'; }

VARS="version version_comment server_id server_uuid gtid_mode enforce_gtid_consistency log_bin binlog_format
binlog_row_image binlog_expire_logs_seconds log_replica_updates read_only super_read_only character_set_server
collation_server time_zone system_time_zone sql_mode lower_case_table_names default_storage_engine
transaction_isolation datadir log_bin_basename relay_log_basename innodb_page_size"

# 1) 버전·엔진·GTID·서버 식별자·binlog 보존·디스크·쓰기 경로 확인
preflight() {
  local fail=0 q
  q="SELECT CONCAT_WS('|', $(for v in $VARS; do printf "CONCAT('%s=',IFNULL(@@GLOBAL.%s,'NULL'))," "$v" "$v"; done | sed 's/,$//'))"
  for side in src tgt; do
    log "== $side 설정"
    $side -e "$q" | tr '|' '\n' | tee "$WORK_DIR/preflight.$side.txt"
  done
  # 서버 식별자는 서로 달라야 함
  [[ $(grep server_id= "$WORK_DIR/preflight.src.txt") != $(grep server_id= "$WORK_DIR/preflight.tgt.txt") ]] || { log "실패: server_id 같음"; fail=1; }
  [[ $(grep server_uuid= "$WORK_DIR/preflight.src.txt") != $(grep server_uuid= "$WORK_DIR/preflight.tgt.txt") ]] || { log "실패: server_uuid 같음"; fail=1; }
  for side in src tgt; do
    for need in gtid_mode=ON enforce_gtid_consistency=ON log_bin=1 binlog_format=ROW; do
      grep -qx "$need" "$WORK_DIR/preflight.$side.txt" || { log "실패: $side $need 아님"; fail=1; }
    done
  done
  grep -qx 'binlog_expire_logs_seconds=2592000' "$WORK_DIR/preflight.src.txt" || { log "실패: 원본 binlog 보존 30일 아님"; fail=1; }
  grep -qx 'super_read_only=1' "$WORK_DIR/preflight.tgt.txt" || { log "실패: 신규 DB가 super_read_only 아님(조기 쓰기 방지)"; fail=1; }
  # 환경 차이를 확인할 설정은 값 비교만 출력 (버전·식별자·경로는 다를 수 있음)
  log "== 원본·신규 설정 차이"
  diff "$WORK_DIR/preflight.src.txt" "$WORK_DIR/preflight.tgt.txt" || true
  log "== 원본 이전 범위: 엔진·객체"
  src -e "SELECT ENGINE, COUNT(*), ROUND(SUM(DATA_LENGTH+INDEX_LENGTH)/1024/1024,1) AS mb FROM information_schema.TABLES
          WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_TYPE='BASE TABLE' GROUP BY ENGINE"
  src -e "SELECT 'non_innodb', COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_TYPE='BASE TABLE' AND ENGINE<>'InnoDB'
          UNION ALL SELECT 'views', COUNT(*) FROM information_schema.VIEWS WHERE TABLE_SCHEMA='$DB_NAME'
          UNION ALL SELECT 'triggers', COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA='$DB_NAME'
          UNION ALL SELECT 'routines', COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA='$DB_NAME'
          UNION ALL SELECT 'events', COUNT(*) FROM information_schema.EVENTS WHERE EVENT_SCHEMA='$DB_NAME'
          UNION ALL SELECT 'no_primary_key', COUNT(*) FROM information_schema.TABLES t WHERE t.TABLE_SCHEMA='$DB_NAME' AND t.TABLE_TYPE='BASE TABLE'
            AND NOT EXISTS (SELECT 1 FROM information_schema.TABLE_CONSTRAINTS c WHERE c.TABLE_SCHEMA=t.TABLE_SCHEMA AND c.TABLE_NAME=t.TABLE_NAME AND c.CONSTRAINT_TYPE='PRIMARY KEY')" \
    | tee "$WORK_DIR/preflight.objects.txt"
  awk '$1=="non_innodb"&&$2>0{exit 1}' "$WORK_DIR/preflight.objects.txt" || { log "실패: InnoDB 아닌 테이블 있음"; fail=1; }
  awk '$1=="no_primary_key"&&$2>0{exit 1}' "$WORK_DIR/preflight.objects.txt" || { log "실패: PK 없는 테이블 있음(체크섬·복제 위험)"; fail=1; }
  disk
  log "== 원본 쓰기 경로: 접속 중인 계정 (애플리케이션 외 쓰기 주체 확인용)"
  src -e "SELECT USER, SUBSTRING_INDEX(HOST,':',1), COUNT(*) FROM information_schema.PROCESSLIST GROUP BY 1,2"
  return $fail
}

# binlog·데이터 용량과 덤프 저장 경로 여유
disk() {
  log "== 원본 binlog 합계(MB)·파일 수"
  src -e "SHOW BINARY LOGS" | awk '{s+=$2;n++} END{printf "binlog_mb=%.1f files=%d\n", s/1024/1024, n}'
  for side in src tgt; do
    log "== $side 데이터 용량(MB)"
    $side -e "SELECT ROUND(SUM(DATA_LENGTH+INDEX_LENGTH)/1024/1024,1) FROM information_schema.TABLES WHERE TABLE_SCHEMA='$DB_NAME'"
  done
  # DB 서버의 파일시스템 여유는 각 DB 서버에서 df 또는 CloudWatch Agent disk 지표로 확인 (이 서버 df는 덤프 저장 경로만 해당)
  log "== 덤프 저장 경로 파일시스템"
  df -h "$WORK_DIR"
}

# 2) 일관된 덤프와 그 시점 GTID 집합을 함께 얻는다
#   --single-transaction: InnoDB 일관 읽기. --source-data=2: 짧은 FLUSH TABLES WITH READ LOCK으로 덤프 시점과 GTID를 일치시킴
#   --set-gtid-purged=ON: 덤프 시점 gtid_executed를 SET @@GLOBAL.gtid_purged로 기록
copy() {
  local dump="$WORK_DIR/dump.sql" t0 t1
  log "덤프 시작 (부하 유지 상태)"
  t0=$(date +%s.%N)
  mysqldump --defaults-extra-file="$SRC_CNF" --single-transaction --source-data=2 --set-gtid-purged=ON \
    --routines --events --triggers --hex-blob --default-character-set=utf8mb4 \
    --databases "$DB_NAME" > "$dump"
  t1=$(date +%s.%N)
  log "덤프 완료 $(du -h "$dump" | cut -f1), $(el "$t0" "$t1")s"
  grep -m1 -A3 'GTID_PURGED' "$dump" | tee "$WORK_DIR/dump.gtid.txt"
  # 신규 DB를 비운 상태에서만 복원 (기존 데이터를 덮어쓰지 않음)
  if [[ $(tgt -e "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='$DB_NAME'") != 0 ]]; then
    log "중단: 신규 DB에 $DB_NAME 이 이미 있음. 직접 확인 후 정리할 것"; return 1
  fi
  # 복원 동안만 읽기 전용을 풀고, 끝나면 다시 켠다 (신규 DB는 격리돼 있어 이 사이 다른 쓰기 주체가 없어야 함)
  tgt -e "STOP REPLICA; RESET REPLICA ALL; SET GLOBAL super_read_only=OFF; SET GLOBAL read_only=OFF; RESET BINARY LOGS AND GTIDS"
  log "복원 시작"
  t0=$(date +%s.%N)
  mysql --defaults-extra-file="$TGT_CNF" < "$dump"
  t1=$(date +%s.%N)
  tgt -e "SET GLOBAL super_read_only=ON"
  log "복원 완료 $(el "$t0" "$t1")s, 신규 gtid_executed: $(tgt -e 'SELECT @@GLOBAL.gtid_executed')"
}

# 3) GTID 자동 위치 지정 비동기 복제
replicate() {
  tgt -e "CHANGE REPLICATION SOURCE TO SOURCE_HOST='$SRC_HOST', SOURCE_PORT=${SRC_PORT:-3306},
      SOURCE_USER='$REPL_USER', SOURCE_PASSWORD='$REPL_PASSWORD', SOURCE_AUTO_POSITION=1, SOURCE_SSL=1,
      SOURCE_CONNECT_RETRY=10, SOURCE_RETRY_COUNT=8640; START REPLICA"
  sleep 2
  status
}

status() {
  local sg
  # 세로 출력은 컬럼 이름이 필요해 -N 없이 조회
  mysql --defaults-extra-file="$TGT_CNF" -e "SHOW REPLICA STATUS\G" | grep -E 'Replica_(IO|SQL)_Running:|Seconds_Behind_Source|Last_(IO|SQL)_Errno|Last_(IO|SQL)_Error:|Auto_Position|Retrieved_Gtid_Set|Executed_Gtid_Set' || true
  sg=$(src -e "SELECT @@GLOBAL.gtid_executed")
  echo "missing_gtid_on_target=$(tgt -e "SELECT GTID_SUBTRACT('$sg', @@GLOBAL.gtid_executed)")"
  tgt -e "SELECT WORKER_ID, LAST_ERROR_NUMBER, LAST_ERROR_MESSAGE FROM performance_schema.replication_applier_status_by_worker WHERE LAST_ERROR_NUMBER<>0"
}

# 4) 스키마 정의 비교. 판정은 information_schema의 정의 값으로 한다
#   SHOW CREATE TABLE 문자열은 같은 정의도 생성 경로에 따라 표기가 달라질 수 있어(예: 컬럼 CHARACTER SET 표시) 참고용으로만 남김
schema_diff() {
  local d="'$DB_NAME'" side cnf_var
  local q="
SELECT 'T',TABLE_NAME,ENGINE,TABLE_COLLATION,ROW_FORMAT,CREATE_OPTIONS,TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA=$d ORDER BY 2;
SELECT 'C',TABLE_NAME,ORDINAL_POSITION,COLUMN_NAME,COLUMN_TYPE,CHARACTER_SET_NAME,COLLATION_NAME,IS_NULLABLE,IFNULL(COLUMN_DEFAULT,'<NULL>'),EXTRA,GENERATION_EXPRESSION,COLUMN_COMMENT FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=$d ORDER BY 2,3;
SELECT 'I',TABLE_NAME,INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME,IFNULL(SUB_PART,''),COLLATION,INDEX_TYPE,IS_VISIBLE,IFNULL(EXPRESSION,'') FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=$d ORDER BY 2,3,5;
SELECT 'K',TABLE_NAME,CONSTRAINT_NAME,CONSTRAINT_TYPE,ENFORCED FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA=$d ORDER BY 2,3;
SELECT 'CK',CONSTRAINT_NAME,CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=$d ORDER BY 2;
SELECT 'FK',k.TABLE_NAME,k.CONSTRAINT_NAME,k.ORDINAL_POSITION,k.COLUMN_NAME,k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,r.UPDATE_RULE,r.DELETE_RULE
  FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r
  ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME WHERE k.TABLE_SCHEMA=$d ORDER BY 2,3,4;
SELECT 'V',TABLE_NAME,VIEW_DEFINITION,SECURITY_TYPE FROM information_schema.VIEWS WHERE TABLE_SCHEMA=$d ORDER BY 2;
SELECT 'TR',TRIGGER_NAME,EVENT_MANIPULATION,EVENT_OBJECT_TABLE,ACTION_TIMING,ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=$d ORDER BY 2;
SELECT 'R',ROUTINE_NAME,ROUTINE_TYPE,ROUTINE_DEFINITION FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=$d ORDER BY 2;
SELECT 'E',EVENT_NAME,EVENT_DEFINITION,STATUS FROM information_schema.EVENTS WHERE EVENT_SCHEMA=$d ORDER BY 2;"
  src -e "$q" > "$WORK_DIR/schema.src.txt"
  tgt -e "$q" > "$WORK_DIR/schema.tgt.txt"
  for side in SRC TGT; do
    cnf_var=${side}_CNF
    mysqldump --defaults-extra-file="${!cnf_var}" --no-data --routines --events --triggers --skip-dump-date \
      --set-gtid-purged=OFF --databases "$DB_NAME" | sed -E -e 's/ AUTO_INCREMENT=[0-9]+//' -e '/^-- Host:/d' > "$WORK_DIR/schema.$side.sql"
  done
  diff -u "$WORK_DIR/schema.SRC.sql" "$WORK_DIR/schema.TGT.sql" > "$WORK_DIR/schema.showcreate.diff" \
    || log "참고: SHOW CREATE 표기 차이 $(grep -c '^[-+] ' "$WORK_DIR/schema.showcreate.diff")줄 (schema.showcreate.diff)"
  if diff -u "$WORK_DIR/schema.src.txt" "$WORK_DIR/schema.tgt.txt" > "$WORK_DIR/schema.diff"; then
    log "스키마 정의 일치: 테이블 $(grep -c '^T' "$WORK_DIR/schema.src.txt"), 컬럼 $(grep -c '^C' "$WORK_DIR/schema.src.txt"),"\
" 인덱스 항목 $(grep -c '^I' "$WORK_DIR/schema.src.txt"), CHECK $(grep -c '^CK' "$WORK_DIR/schema.src.txt"), FK 항목 $(grep -c '^FK' "$WORK_DIR/schema.src.txt")"
  else
    log "스키마 정의 차이 있음: $WORK_DIR/schema.diff"; return 1
  fi
}

# 5) pt-table-checksum: 원본에서 구간 체크섬을 계산하고 복제로 신규 DB에서 같은 계산을 재현해 비교
#   원본 binlog_format=ROW여도 도구가 세션을 STATEMENT로 바꿔 검사 SQL을 복제시킴
checksum() {
  # 신규 DB는 원본 processlist의 복제 연결로 찾는다. 3.7.0(3.7.1 소스도 같음)의 dsn 표 방식은 복제본 대기 단계에서
  # 원본 접속 정보를 잃어 로컬 소켓으로 접속을 시도하는 문제가 있어 쓰지 않음 (2026-10-16 리허설에서 확인)
  # 신규 DB 접속 계정은 원본 DSN의 F=(임시 접속 파일)를 그대로 물려받으므로 양쪽에 같은 검사 계정이 있어야 함
  log "pt-table-checksum 시작 ($($PT_CMD --version))"
  $PT_CMD "h=$SRC_HOST,P=${SRC_PORT:-3306},F=$SRC_CNF" --databases="$DB_NAME" \
    --recursion-method=processlist --replicate=percona.checksums \
    --no-check-binlog-format --chunk-time=0.5 --max-lag=2 --check-interval=1 \
    --progress=time,30 2>&1 | tee "$WORK_DIR/checksum.out"
  local rc=${PIPESTATUS[0]}
  log "pt-table-checksum 종료 코드 $rc (0이어야 차이·오류·건너뜀 없음)"
  # 결과 표에서 차이 구간 직접 조회 (도구 문서의 확인 쿼리)
  tgt -e "SELECT db, tbl, SUM(this_cnt) AS rows_cnt, COUNT(*) AS chunks
          FROM percona.checksums WHERE (source_cnt <> this_cnt OR source_crc <> this_crc OR ISNULL(source_crc) <> ISNULL(this_crc))
          GROUP BY db, tbl" | tee "$WORK_DIR/checksum.diff"
  [[ $rc == 0 && ! -s "$WORK_DIR/checksum.diff" ]]
}

case "${1:-}" in
  preflight) preflight ;;
  copy) copy ;;
  replicate) replicate ;;
  status) status ;;
  schema-diff) schema_diff ;;
  checksum) checksum ;;
  disk) disk ;;
  *) echo "사용법: $0 preflight|copy|replicate|status|schema-diff|checksum|disk"; exit 2 ;;
esac
