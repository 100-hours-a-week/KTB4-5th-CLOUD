"""실제 Docker와 서버를 사용하지 않고 배포·복구 분기를 검증한다."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_BIN") or (
    "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
)
NEW = "ghcr.io/team/backend@sha256:" + "a" * 64
OLD = "ghcr.io/team/backend@sha256:" + "b" * 64
FRONTEND = "ghcr.io/team/frontend:old"
DOCKER = r"""#!/usr/bin/env bash
set -eu
if [[ "$1" == inspect ]]; then
  if [[ "$*" == *Config.Image* ]]; then cat .mockstate; exit; fi
  if [[ "$*" != *State.Status* ]]; then echo healthy; exit; fi
  state=running/healthy
  if [[ "$(cat .mockstate)" == "$NEW_IMAGE" ]]; then
    [[ "$SCENARIO" != health_failure ]] || state=running/unhealthy
    [[ "$SCENARIO" != missing_health ]] || state=running/missing
  fi
  echo "$state"
  exit
fi
shift
envfile=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-name|-f|--profile) shift 2 ;;
    --env-file) envfile="$2"; shift 2 ;;
    *) break ;;
  esac
done
case "$1" in
  config)
    if [[ "$*" == *--format* ]]; then cat .mockconfig; fi
    ;;
  ps)
    if [[ "$*" == *mysql* ]]; then
      if [[ -f .mockmysql ]]; then echo mysql-container-id; fi
    elif [[ -f .mockstate ]]; then
      echo container-id
    fi
    ;;
  pull)
    [[ "$SCENARIO" != pull_failure ]]
    ;;
  up)
    if [[ "$*" == *mysql* ]]; then touch .mockmysql; exit; fi
    key=BE_IMAGE
    [[ "$*" != *frontend* ]] || key=FE_IMAGE
    value=$(sed -n "s/^${key}=//p" "$envfile")
    printf '%s' "$value" > .mockstate
    [[ "$SCENARIO" != up_failure || "$value" != "$NEW_IMAGE" ]]
    ;;
  stop)
    echo stopped > .mockstopped
    ;;
  *) echo "예상하지 못한 Docker 명령" >&2; exit 90 ;;
esac
"""

class DeployTest(unittest.TestCase):
    def run_deploy(self, scenario, first=False, image=NEW, has_mysql=False, schema=True, service="backend"):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            bins = path / "bin"
            bins.mkdir()
            service_env_dir = path / "env"
            service_env_dir.mkdir()
            (path / "prod-compose.yaml").write_text("services: {}\n")
            original = f"FE_IMAGE={FRONTEND}\nBE_IMAGE={OLD}\nCERTBOT_WEBROOT=./certbot/www\n"
            (path / ".env").write_text(original)
            if service == "backend":
                original_service = (
                    "SERVER_PORT=8080\n"
                    "SPRING_DATASOURCE_URL=jdbc:mysql://mysql:3306/app\n"
                    "SPRING_DATASOURCE_USERNAME=appuser\n"
                    "SPRING_DATASOURCE_PASSWORD=old-secret\n"
                    "SPRING_JPA_HIBERNATE_DDL_AUTO=validate\n"
                    "MYSQL_DATABASE=app\n"
                    "MYSQL_USER=appuser\n"
                    "MYSQL_PASSWORD=old-secret\n"
                    "MYSQL_ROOT_PASSWORD=old-root-secret\n"
                )
            else:
                original_service = (
                    "NODE_ENV=production\nPORT=3000\nHOSTNAME=0.0.0.0\n"
                    "API_INTERNAL_URL=http://old-backend:8080\n"
                )
            service_env_file = service_env_dir / f"{service}.env"
            service_env_file.write_text(original_service)
            services = {"frontend": {"image": FRONTEND}, "backend": {"image": OLD}}
            if has_mysql:
                services["mysql"] = {"image": "mysql:8.4"}
                if schema:
                    (path / "db").mkdir()
                    (path / "db/schema.sql").write_text("CREATE TABLE sample (id BIGINT);\n")
            (path / ".mockconfig").write_text(json.dumps({"services": services}))
            if not first:
                (path / ".mockstate").write_text(OLD if service == "backend" else FRONTEND)
            stubs = {
                "docker": DOCKER,
                "python3": '#!/usr/bin/env bash\nexec "$PYTHON_BIN" "$@"\n',
                # 이 테스트는 잠금 분기가 아닌 배포 상태 전이를 검사한다.
                "flock": "#!/usr/bin/env bash\nexit 0\n",
            }
            for name, body in stubs.items():
                target = bins / name
                target.write_text(body, encoding="utf-8", newline="\n")
                target.chmod(0o755)
            env = os.environ | {
                "PATH": str(bins) + os.pathsep + os.environ["PATH"],
                "PYTHON_BIN": sys.executable.replace("\\", "/"),
                "NEW_IMAGE": NEW, "SCENARIO": scenario,
            }
            if service == "backend":
                incoming = (
                    "SERVER_PORT=8080\n"
                    "SPRING_DATASOURCE_URL=jdbc:mysql://mysql:3306/app\n"
                    "SPRING_DATASOURCE_USERNAME=appuser\n"
                    "SPRING_DATASOURCE_PASSWORD=new-secret\n"
                    "SPRING_JPA_HIBERNATE_DDL_AUTO=validate\n"
                    "MYSQL_DATABASE=app\n"
                    "MYSQL_USER=appuser\n"
                    "MYSQL_PASSWORD=new-secret\n"
                    "MYSQL_ROOT_PASSWORD=new-root-secret\n"
                )
            else:
                incoming = ""
            result = subprocess.run(
                [BASH, str(ROOT / "scripts/deploy-v1.sh").replace("\\", "/"),
                 service, image, "test", "prod-compose.yaml"],
                input=incoming, text=True, capture_output=True, cwd=path, env=env,
                timeout=20, encoding="utf-8",
            )
            self.assertNotIn("new-secret", result.stdout + result.stderr)
            return result, (path / ".env").read_text(), (
                service_env_file
            ).read_text(), (
                (path / ".mockstate").read_text() if (path / ".mockstate").exists() else ""
            ), (path / ".mockstopped").exists(), original, original_service

    def test_success_preserves_other_service(self):
        result, env, service_env, running, _, _, _ = self.run_deploy("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(running, NEW)
        self.assertIn("FE_IMAGE=" + FRONTEND, env)
        self.assertIn("BE_IMAGE=" + NEW, env)
        self.assertIn("CERTBOT_WEBROOT=./certbot/www", env)
        self.assertIn("MYSQL_PASSWORD=new-secret", service_env)

    def test_frontend_updates_only_frontend_configuration(self):
        result, env, service_env, running, _, _, _ = self.run_deploy(
            "success", service="frontend"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(running, NEW)
        self.assertIn("FE_IMAGE=" + NEW, env)
        self.assertIn("BE_IMAGE=" + OLD, env)
        self.assertEqual(service_env.strip(), "")

    def test_pull_failure_preserves_configuration(self):
        result, env, service_env, running, _, original, original_service = self.run_deploy("pull_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FE_IMAGE=" + FRONTEND, env)
        self.assertIn("BE_IMAGE=" + OLD, env)
        self.assertIn("CERTBOT_WEBROOT=./certbot/www", env)
        self.assertEqual(service_env, original_service)
        self.assertEqual(running, OLD)

    def test_health_and_start_failures_roll_back(self):
        for scenario in ("health_failure", "up_failure", "missing_health"):
            with self.subTest(scenario=scenario):
                result, env, service_env, running, _, _, original_service = self.run_deploy(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(running, OLD)
                self.assertIn("BE_IMAGE=" + OLD, env)
                self.assertEqual(service_env, original_service)

    def test_first_failure_stops_failed_container(self):
        result, env, service_env, _, stopped, original, original_service = self.run_deploy("health_failure", first=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(env, original)
        self.assertEqual(service_env, original_service)
        self.assertTrue(stopped)

    def test_mutable_tag_is_rejected(self):
        result, env, service_env, running, _, original, original_service = self.run_deploy("success", image="ghcr.io/team/backend:latest")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(env, original)
        self.assertEqual(service_env, original_service)
        self.assertEqual(running, OLD)

    def test_dev_starts_mysql_when_schema_exists(self):
        result, _, _, running, _, _, _ = self.run_deploy("success", has_mysql=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(running, NEW)

    def test_dev_rejects_missing_schema(self):
        result, env, service_env, running, _, original, original_service = self.run_deploy(
            "success", has_mysql=True, schema=False
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("db/schema.sql", result.stderr)
        self.assertIn("FE_IMAGE=" + FRONTEND, env)
        self.assertIn("BE_IMAGE=" + OLD, env)
        self.assertIn("CERTBOT_WEBROOT=./certbot/www", env)
        self.assertEqual(service_env, original_service)
        self.assertEqual(running, OLD)

if __name__ == "__main__":
    unittest.main()
