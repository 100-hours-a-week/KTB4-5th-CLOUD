#!/usr/bin/env python3
"""DB 직접 부하·쓰기 중단 전환 테스트 도구 (BE를 거치지 않는 테스트 클라이언트).

하위 명령
  seed    원본 app 스키마에 반복 가능한 기준 데이터를 만든다
  run     부하를 계속 발생시키고, 제어 파일로 단계 표시·전환·종료를 받는다
  verify  전환 후 커밋 확인된 쓰기가 신규 DB에 정확히 한 번씩 있는지 확인한다

연결 전환 방식은 "테스트 클라이언트 재연결"이다. 이 결과를 BE 포함 전환 성공으로 해석하지 않는다.
비밀번호는 환경변수(SRC_PASSWORD, TGT_PASSWORD)로만 받고 출력하지 않는다.
"""
import argparse
import csv
import json
import math
import os
import random
import sys
import threading
import time
from datetime import datetime, timezone

import pymysql

MARK = "migtest"  # 시험 쓰기 표식. notifications.title = migtest:<run>:<seq>


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="microseconds")


def connect(prefix, autocommit=True, use_db=True):
    """SRC_* / TGT_* 환경변수로 접속한다. 복원 전 신규 DB처럼 스키마가 없을 수 있으면 use_db=False."""
    return pymysql.connect(
        host=os.environ[f"{prefix}_HOST"],
        port=int(os.environ.get(f"{prefix}_PORT", "3306")),
        user=os.environ[f"{prefix}_USER"],
        password=os.environ[f"{prefix}_PASSWORD"],
        # 시험 부하는 LOAD_DB(예: 원본의 app과 분리한 migtest 스키마)에 쓴다. 없으면 DB_NAME의 첫 번째
        database=(os.environ.get("LOAD_DB") or os.environ.get("DB_NAME", "dameokja").split()[0]) if use_db else None,
        autocommit=autocommit,
        connect_timeout=3,
        read_timeout=10,
        write_timeout=10,
        charset="utf8mb4",
    )


def q1(conn, sql, args=None):
    with conn.cursor() as c:
        c.execute(sql, args)
        return c.fetchone()


# ---------------------------------------------------------------- seed
PAIRS_SQL = ("SELECT u.user_id, r.refrigerator_id FROM users u JOIN refrigerators r "
             "ON r.name = u.nickname WHERE u.nickname LIKE 'mt%' ORDER BY u.user_id")


def seed(a):
    """시험용 회원·냉장고(이름 mt<번호>)와 알림·수신자를 추가한다. 기존 데이터(stage 사본 등)는 건드리지 않는다.
    같은 인자면 같은 행 구성이 되며, 이미 시험용 회원이 있으면 건너뛴다."""
    conn = connect("SRC")
    with conn.cursor() as c:
        c.execute("SELECT COUNT(*) FROM users WHERE nickname LIKE 'mt%'")
        if c.fetchone()[0] > 0:
            print("시험용 데이터가 이미 있어 seed를 건너뜀")
            return
        c.execute("SET SESSION cte_max_recursion_depth = 10000000")
        # 닉네임은 2~10자 UNIQUE. 회원 mt<n>과 냉장고 mt<n>을 이름으로 짝지음
        c.execute(
            "INSERT INTO users (nickname, profile_image_key) "
            "WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n < %s) "
            "SELECT CONCAT('mt', n), 'default.png' FROM s", (a.users,))
        c.execute(
            "INSERT INTO refrigerators (name, expired_count_month) "
            "WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n < %s) "
            "SELECT CONCAT('mt', n), DATE_FORMAT(NOW(), '%%Y-%%m') FROM s", (a.users,))
        c.execute("CREATE TEMPORARY TABLE mt_pairs (k INT PRIMARY KEY, user_id BIGINT, refrigerator_id BIGINT)")
        c.execute("INSERT INTO mt_pairs SELECT ROW_NUMBER() OVER (ORDER BY user_id) - 1, user_id, refrigerator_id FROM (" + PAIRS_SQL + ") p")
        done = 0
        while done < a.notifications:  # 큰 트랜잭션을 피하려고 나눠 넣음
            n = min(50000, a.notifications - done)
            c.execute(
                "INSERT INTO notifications (type, title, body, refrigerator_id) "
                "WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n < %s) "
                "SELECT 'EXPIRED', CONCAT('seed:', s.n + %s), REPEAT('본문', 20), p.refrigerator_id "
                "FROM s JOIN mt_pairs p ON p.k = (s.n + %s) %% %s",
                (n, done, done, a.users))
            c.execute(
                "INSERT INTO notification_recipients (user_id, notification_id) "
                "SELECT p.user_id, x.notification_id FROM notifications x JOIN mt_pairs p ON p.refrigerator_id = x.refrigerator_id "
                "WHERE x.title LIKE 'seed:%%' AND x.notification_id > "
                "(SELECT COALESCE(MAX(notification_id), 0) FROM notification_recipients)")
            done += n
        c.execute("DROP TEMPORARY TABLE mt_pairs")
    print(json.dumps({"seeded_users": a.users, "seeded_notifications": a.notifications}))


# ---------------------------------------------------------------- run
class Gate:
    """새 쓰기 진입 제어. 닫힌 뒤 진행 중 작업 수가 0이 되는 것을 경쟁 조건 없이 확인한다."""

    def __init__(self):
        self.cv = threading.Condition()
        self.open = True
        self.inflight = 0
        self.epoch = "source"  # 현재 쓰기 대상 DB

    def enter(self, stop):
        with self.cv:
            while not self.open and not stop.is_set():
                self.cv.wait(0.05)
            if stop.is_set():
                return None
            self.inflight += 1
            return self.epoch

    def leave(self):
        with self.cv:
            self.inflight -= 1
            self.cv.notify_all()

    def close_and_drain(self, timeout):
        with self.cv:
            self.open = False
            end = time.monotonic() + timeout
            while self.inflight > 0:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self.cv.wait(left)
            return True

    def reopen(self, epoch):
        with self.cv:
            self.epoch = epoch
            self.open = True
            self.cv.notify_all()


class Runner:
    def __init__(self, a):
        self.a = a
        self.stop = threading.Event()
        self.gate = Gate()
        self.phase = "baseline"
        self.lock = threading.Lock()
        self.samples = []  # (phase, kind, wall_ts, latency_ms, ok, db, err)
        self.acked = []  # (seq, db) 커밋 응답을 받은 시험 쓰기
        self.unknown = []  # 커밋 결과가 불명확한 시험 쓰기
        self.seq = 0
        self.first_target_commit = None
        self.cut = {}  # 전환 단계별 기록
        self.target_warm = threading.Event()
        self.warmed = 0  # 신규 연결을 연 작업자 수
        with connect("SRC").cursor() as c:  # seed가 만든 (회원, 냉장고) 짝만 사용. 기존 데이터의 FK를 추측하지 않음
            c.execute(PAIRS_SQL)
            self.pairs = c.fetchall()
        if not self.pairs:
            sys.exit("시험용 회원·냉장고가 없음. 먼저 seed를 실행")

    def next_seq(self):
        with self.lock:
            self.seq += 1
            return self.seq

    def record(self, *row):
        with self.lock:
            self.samples.append(row)

    # 처음에는 원본 연결만 연다. 전환 요청을 받으면 측정 시작 전에 신규 연결을 미리 열어 둔다(warm)
    # 신규 연결은 전환 전까지 읽기 전용 DB에 붙어 있을 뿐 쓰지 않는다
    def conns(self):
        return {"source": connect("SRC", autocommit=False)}

    def warm(self, conns):
        if self.target_warm.is_set() and "target" not in conns:
            try:
                conns["target"] = connect("TGT", autocommit=False)
                with self.lock:
                    self.warmed += 1
            except Exception:
                time.sleep(0.2)

    def pace(self, rate, nthreads):
        return nthreads / rate if rate > 0 else None

    def writer(self, wid):
        conns = self.conns()
        interval = self.pace(self.a.write_rate, self.a.writers)
        nxt = time.monotonic()
        while not self.stop.is_set():
            self.warm(conns)
            nxt += interval
            db = self.gate.enter(self.stop)
            if db is None:
                break
            seq = self.next_seq()
            t0 = time.monotonic()
            ok, err = True, ""
            try:
                conn = conns[db]
                uid, rid = random.choice(self.pairs)
                with conn.cursor() as c:
                    # 알림 생성 + 수신자 추가 + 이전 알림 읽음 처리를 한 트랜잭션으로 (binlog 관찰과 같은 다중 테이블 트랜잭션)
                    c.execute("INSERT INTO notifications (type, title, body, refrigerator_id) VALUES ('EXPIRED', %s, %s, %s)",
                              (f"{MARK}:{self.a.run_id}:{seq}", f"w={db}", rid))
                    nid = c.lastrowid
                    c.execute("INSERT INTO notification_recipients (user_id, notification_id) VALUES (%s, %s)", (uid, nid))
                    c.execute("UPDATE notification_recipients SET read_at = NOW(6) WHERE notification_recipient_id = %s",
                              (random.randint(1, max(1, nid - 1)),))
                conn.commit()
                with self.lock:
                    self.acked.append((seq, db))
                    if db == "target" and self.first_target_commit is None:
                        self.first_target_commit = (time.monotonic(), now_iso())
            except Exception as e:  # 연결 오류 등. 커밋 여부가 불명확하므로 verify에서 별도 집계
                ok, err = False, type(e).__name__ + ":" + str(e)[:120]
                with self.lock:
                    self.unknown.append((seq, db))
                try:
                    conns[db].close()
                except Exception:
                    pass
                try:
                    conns[db] = connect("SRC" if db == "source" else "TGT", autocommit=False)
                except Exception:
                    time.sleep(0.2)
            finally:
                self.gate.leave()
            self.record(self.phase, "write", time.time(), (time.monotonic() - t0) * 1000, ok, db, err)
            time.sleep(max(0, nxt - time.monotonic()))

    def reader(self, rid):
        conns = self.conns()
        interval = self.pace(self.a.read_rate, self.a.readers)
        nxt = time.monotonic()
        while not self.stop.is_set():
            self.warm(conns)
            nxt += interval
            db = self.gate.epoch  # 읽기는 진입 제어 대상이 아님. 전환 후 신규 DB로 이동
            t0 = time.monotonic()
            ok, err = True, ""
            try:
                with conns[db].cursor() as c:
                    c.execute("SELECT notification_id, title, created_at FROM notifications WHERE refrigerator_id = %s "
                              "ORDER BY created_at DESC, notification_id DESC LIMIT 20", (random.choice(self.pairs)[1],))
                    c.fetchall()
                conns[db].commit()
            except Exception as e:
                ok, err = False, type(e).__name__ + ":" + str(e)[:120]
                try:
                    conns[db] = connect("SRC" if db == "source" else "TGT", autocommit=False)
                except Exception:
                    time.sleep(0.2)
            self.record(self.phase, "read", time.time(), (time.monotonic() - t0) * 1000, ok, db, err)
            time.sleep(max(0, nxt - time.monotonic()))

    def monitor(self):
        """1초마다 원본·신규 상태를 남긴다. 미적용 GTID 수와 복제 오류로 지연을 판단한다."""
        path = os.path.join(self.a.out, "monitor.csv")
        src = connect("SRC")
        tgt = connect("TGT", use_db=False) if os.environ.get("TGT_HOST") else None
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "phase", "src_threads_running", "tgt_threads_running", "missing_gtid_trx",
                        "seconds_behind", "io_running", "sql_running", "last_error"])
            while not self.stop.is_set():
                row = [now_iso(), self.phase]
                try:
                    row.append(q1(src, "SHOW GLOBAL STATUS LIKE 'Threads_running'")[1])
                    sg = q1(src, "SELECT @@GLOBAL.gtid_executed")[0]
                    if tgt:
                        row.append(q1(tgt, "SHOW GLOBAL STATUS LIKE 'Threads_running'")[1])
                        missing = q1(tgt, "SELECT GTID_SUBTRACT(%s, @@GLOBAL.gtid_executed)", (sg,))[0]
                        row.append(gtid_count(missing))
                        with tgt.cursor(pymysql.cursors.DictCursor) as c:
                            c.execute("SHOW REPLICA STATUS")
                            r = c.fetchone() or {}
                        row += [r.get("Seconds_Behind_Source"), r.get("Replica_IO_Running"), r.get("Replica_SQL_Running"),
                                (r.get("Last_IO_Error") or "") + (r.get("Last_SQL_Error") or "")]
                except Exception as e:
                    row.append("monitor_error:" + type(e).__name__)
                w.writerow(row)
                f.flush()
                time.sleep(1)

    def control(self):
        """제어 파일: phase(현재 단계 이름), cutover(전환 요청, 내용은 옵션 JSON), stop(종료)."""
        d = self.a.control
        last = None  # 파일 내용이 바뀔 때만 반영. 전환 중 내부에서 바꾼 단계(cutover·after_cutover)를 덮어쓰지 않음
        while not self.stop.is_set():
            p = os.path.join(d, "phase")
            if os.path.exists(p):
                with open(p) as f:
                    v = f.read().strip()
                if v and v != last:
                    self.phase = last = v
            c = os.path.join(d, "cutover")
            if os.path.exists(c):
                with open(c) as f:
                    opt = json.loads(f.read() or "{}")
                os.remove(c)
                self.cutover(opt)
            if os.path.exists(os.path.join(d, "stop")):
                self.stop.set()
            time.sleep(0.1)

    # ------------------------------------------------------------ 전환
    def cutover(self, opt):
        """새 쓰기 중지 → 진행 중 쓰기 완료 → 원본 쓰기 차단 → 최종 GTID → 신규 적용 확인 → 승격·전환 → 재개."""
        budget = float(opt.get("budget_s", 1.0))  # 판정 기준. 넘어도 안전 단계는 생략하지 않음
        hard = float(opt.get("hard_timeout_s", 10.0))  # 이 시간 안에 신규 적용이 안 되면 취소
        rec, m = self.cut, time.monotonic

        def mark(name):
            rec.setdefault("steps", []).append({"step": name, "t_ms": round((m() - t0) * 1000, 3), "wall": now_iso()})

        src = connect("SRC")
        tgt = connect("TGT", use_db=False)
        # 측정 시작 전 준비: 모든 작업자가 신규 연결을 열 때까지 기다린다 (쓰기는 계속 원본으로 진행)
        self.target_warm.set()
        need = self.a.writers + self.a.readers
        end = m() + 10
        while self.warmed < need and m() < end:
            time.sleep(0.01)
        rec["warmed_connections"] = f"{self.warmed}/{need}"
        self.phase = "cutover"
        t0 = m()
        rec.update({"start_wall": now_iso(), "budget_s": budget})
        mark("1_new_writes_stopped")
        if not self.gate.close_and_drain(hard):
            return self.abort(src, rec, t0, "inflight_drain_timeout", mark)
        mark("2_inflight_writes_done")
        q1(src, "SET GLOBAL super_read_only = ON")  # read_only도 함께 켜짐
        mark("3_source_writes_blocked")
        # 변경을 가진 열린 트랜잭션 (읽기 트랜잭션 제외). 0이 아니면 다른 쓰기 주체가 있다는 뜻이라 기록
        trx = q1(src, "SELECT COUNT(*) FROM information_schema.innodb_trx WHERE trx_rows_modified > 0")[0]
        rec["source_open_trx_after_block"] = trx
        gtid = q1(src, "SELECT @@GLOBAL.gtid_executed")[0]
        rec["final_source_gtid"] = gtid
        mark("4_final_gtid_captured")
        left = max(0.001, hard - (m() - t0))
        r = q1(tgt, "SELECT WAIT_FOR_EXECUTED_GTID_SET(%s, %s)", (gtid, left))[0]
        err = q1(tgt, "SELECT COALESCE(MAX(LAST_ERROR_NUMBER), 0) FROM performance_schema.replication_applier_status_by_worker")[0]
        if r != 0 or err:
            rec["wait_result"], rec["applier_error"] = r, err
            return self.abort(src, rec, t0, "target_not_caught_up", mark)
        missing = q1(tgt, "SELECT GTID_SUBTRACT(%s, @@GLOBAL.gtid_executed)", (gtid,))[0]
        if missing:
            return self.abort(src, rec, t0, "gtid_missing_after_wait", mark)
        mark("5_target_applied_final_gtid")
        if opt.get("inject_failure") == "before_promote":  # 실패 분기 시험: 신규 DB 쓰기 전 취소
            return self.abort(src, rec, t0, "injected_before_promote", mark)
        q1(tgt, "STOP REPLICA")
        q1(tgt, "RESET REPLICA ALL")
        q1(tgt, "SET GLOBAL super_read_only = OFF")
        q1(tgt, "SET GLOBAL read_only = OFF")
        mark("6_target_promoted")
        ro = q1(tgt, "SELECT @@GLOBAL.read_only, @@GLOBAL.super_read_only")
        rep = q1(tgt, "SELECT COUNT(*) FROM performance_schema.replication_connection_configuration")[0]
        if ro != (0, 0) or rep:
            rec["target_state"] = {"read_only": ro, "replication_channels": rep}
            # 신규 DB에 쓰기 전이므로 원본 재개가 가능하다
            return self.abort(src, rec, t0, "target_not_writable", mark)
        mark("7_ready_to_resume_on_target")  # 측정 종료 지점
        rec["write_pause_ms"] = rec["steps"][-1]["t_ms"]
        rec["met_budget"] = rec["write_pause_ms"] <= budget * 1000
        rec["result"] = "switched"
        self.gate.reopen("target")
        mark("8_writes_resumed")
        self.phase = "after_cutover"
        threading.Thread(target=self.wait_first_write, args=(t0,), daemon=True).start()

    def abort(self, src, rec, t0, reason, mark):
        """신규 DB에 업무 쓰기를 하기 전 실패: 원본 상태 확인 후 원본에서 재개. 중단 시간도 기록한다."""
        q1(src, "SET GLOBAL super_read_only = OFF")
        q1(src, "SET GLOBAL read_only = OFF")
        ro = q1(src, "SELECT @@GLOBAL.read_only, @@GLOBAL.super_read_only")
        mark("x_source_writable_again")
        rec.update({"result": "aborted", "reason": reason, "source_state_after_abort": ro,
                    "write_pause_ms": rec["steps"][-1]["t_ms"]})
        rec["met_budget"] = rec["write_pause_ms"] <= rec["budget_s"] * 1000
        self.gate.reopen("source")
        self.phase = "after_abort"
        self.dump_cut()

    def wait_first_write(self, t0):
        while self.first_target_commit is None and not self.stop.is_set():
            time.sleep(0.001)
        if self.first_target_commit:
            self.cut["first_target_commit_ms"] = round((self.first_target_commit[0] - t0) * 1000, 3)
            self.cut["first_target_commit_wall"] = self.first_target_commit[1]
        self.dump_cut()

    def dump_cut(self):
        with open(os.path.join(self.a.out, "cutover.json"), "w") as f:
            json.dump(self.cut, f, ensure_ascii=False, indent=2)
        print("CUTOVER", json.dumps(self.cut, ensure_ascii=False))
        sys.stdout.flush()

    def go(self):
        os.makedirs(self.a.out, exist_ok=True)
        os.makedirs(self.a.control, exist_ok=True)
        ths = [threading.Thread(target=self.writer, args=(i,), daemon=True) for i in range(self.a.writers)]
        ths += [threading.Thread(target=self.reader, args=(i,), daemon=True) for i in range(self.a.readers)]
        ths += [threading.Thread(target=self.monitor, daemon=True)]
        for t in ths:
            t.start()
        print(json.dumps({"run_id": self.a.run_id, "started": now_iso()}))
        sys.stdout.flush()
        try:
            self.control()
        finally:
            self.stop.set()
            time.sleep(1)
            self.save()

    def save(self):
        with open(os.path.join(self.a.out, "samples.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["phase", "kind", "ts", "latency_ms", "ok", "db", "error"])
            w.writerows(self.samples)
        with open(os.path.join(self.a.out, "acked.json"), "w") as f:
            json.dump({"run_id": self.a.run_id, "acked": self.acked, "unknown": self.unknown}, f)
        summary = summarize(self.samples)
        with open(os.path.join(self.a.out, "summary.json"), "w") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print("SUMMARY", json.dumps(summary, ensure_ascii=False))


def pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, math.ceil(p / 100 * len(vals)) - 1))  # nearest-rank
    return round(vals[k], 3)


def summarize(samples):
    """단계·종류별 성공 표본의 p50·p95·p99와 오류 수. 오류는 성공 표본에서 빼되 별도로 센다."""
    out = {}
    for ph, kind, ts, lat, ok, db, err in samples:
        k = f"{ph}/{kind}"
        d = out.setdefault(k, {"lat": [], "errors": 0, "t0": ts, "t1": ts})
        d["t1"] = max(d["t1"], ts)
        d["t0"] = min(d["t0"], ts)
        if ok:
            d["lat"].append(lat)
        else:
            d["errors"] += 1
    res = {}
    for k, d in out.items():
        dur = max(1e-9, d["t1"] - d["t0"])
        res[k] = {"ok": len(d["lat"]), "errors": d["errors"], "duration_s": round(dur, 1),
                  "ok_per_s": round(len(d["lat"]) / dur, 1) if dur >= 1 else None,  # 1초 미만 구간은 처리율을 계산하지 않음
                  "p50_ms": pct(d["lat"], 50), "p95_ms": pct(d["lat"], 95), "p99_ms": pct(d["lat"], 99)}
    return res


def gtid_count(s):
    """GTID 집합 문자열의 트랜잭션 수."""
    n = 0
    for part in (s or "").replace("\n", "").split(","):
        for iv in part.strip().split(":")[1:]:
            if "-" in iv:
                lo, hi = iv.split("-")
                n += int(hi) - int(lo) + 1
            elif iv.isdigit():
                n += 1
    return n


# ---------------------------------------------------------------- verify
def verify(a):
    """커밋 응답을 받은 쓰기는 신규 DB에 정확히 1번, 원본에서 커밋된 쓰기는 모두 신규 DB에 있어야 한다."""
    data = json.load(open(os.path.join(a.out, "acked.json")))
    run = data["run_id"]
    like = f"{MARK}:{run}:%"
    sql = "SELECT title, body, COUNT(*) FROM notifications WHERE title LIKE %s GROUP BY title, body"
    src, tgt = connect("SRC"), connect("TGT")

    def rows(conn):
        with conn.cursor() as c:
            c.execute(sql, (like,))
            return {(int(t.rsplit(":", 1)[1]), b[2:]): n for t, b, n in c.fetchall()}

    s, t = rows(src), rows(tgt)
    tseq = {}
    for (seq, db), n in t.items():
        tseq[seq] = tseq.get(seq, 0) + n
    acked = {seq for seq, db in data["acked"]}
    unknown = {seq for seq, db in data["unknown"]}
    src_rows = {k for k in s}
    res = {
        "run_id": run,
        "acked": len(acked),
        "acked_missing_on_target": sorted(x for x in acked if x not in tseq)[:20],
        "acked_missing_count": sum(1 for x in acked if x not in tseq),
        "duplicates_on_target": sorted(x for x, n in tseq.items() if n > 1)[:20],
        "source_committed": len(src_rows),
        "source_rows_missing_on_target": sum(1 for k in src_rows if k not in t),
        "unknown_outcome": len(unknown),
        "unknown_committed_on_target": sum(1 for x in unknown if x in tseq),
        "target_rows_without_ack": sorted(x for x in tseq if x not in acked and x not in unknown)[:20],
    }
    res["pass"] = not (res["acked_missing_count"] or res["duplicates_on_target"]
                       or res["source_rows_missing_on_target"] or res["target_rows_without_ack"])
    cut = os.path.join(a.out, "cutover.json")
    if os.path.exists(cut):
        c = json.load(open(cut))
        if c.get("result") == "switched":
            # 원본 차단 이후 원본에 트랜잭션이 추가되지 않았는지: 현재 원본 GTID 집합이 전환 때 확보한 집합과 같아야 함
            extra = q1(src, "SELECT GTID_SUBTRACT(@@GLOBAL.gtid_executed, %s)", (c["final_source_gtid"],))[0]
            res["source_trx_after_block"] = gtid_count(extra)
            res["pass"] = res["pass"] and res["source_trx_after_block"] == 0
    with open(os.path.join(a.out, "verify.json"), "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["pass"] else 1


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("seed")
    s.add_argument("--users", type=int, default=2000)
    s.add_argument("--notifications", type=int, default=300000)
    r = sp.add_parser("run")
    r.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d%H%M%S"))
    r.add_argument("--write-rate", type=float, default=100)  # 초당 쓰기 트랜잭션
    r.add_argument("--read-rate", type=float, default=200)
    r.add_argument("--writers", type=int, default=8)
    r.add_argument("--readers", type=int, default=8)
    r.add_argument("--out", default="out")
    r.add_argument("--control", default="control")
    v = sp.add_parser("verify")
    v.add_argument("--out", default="out")
    a = p.parse_args()
    if a.cmd == "seed":
        seed(a)
    elif a.cmd == "run":
        Runner(a).go()
    else:
        sys.exit(verify(a))


if __name__ == "__main__":
    main()
