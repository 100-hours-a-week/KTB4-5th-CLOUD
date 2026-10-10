"""AWS 접속 없이 SSM 경로 격리와 배포 실패 조건을 검증한다."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ecs_secrets", ROOT / "scripts/render_ecs_secrets.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class EcsSecretsTest(unittest.TestCase):
    def template(self, service):
        return json.loads((ROOT / f"infra/v2/ecs/dev/{service}.json").read_text(encoding="utf-8"))

    def render(self, service, names):
        with patch.object(MODULE, "parameter_names", return_value=names):
            return MODULE.render(self.template(service), service)

    def test_backend_replaces_removed_names_and_excludes_nginx_and_frontend(self):
        original = self.template("backend")
        result = self.render("backend", [
            "/dameokja-v2-dev/NEW_KEY", "/dameokja-v2-dev/AI_BASE_URL",
            "/dameokja-v2-dev/nginx/default.conf", "/dameokja-v2-dev-fe/FE_ONLY",
        ])
        secrets = result["containerDefinitions"][0].pop("secrets")
        self.assertEqual(secrets, [
            {"name": "AI_BASE_URL", "valueFrom": "/dameokja-v2-dev/AI_BASE_URL"},
            {"name": "NEW_KEY", "valueFrom": "/dameokja-v2-dev/NEW_KEY"},
        ])
        original["containerDefinitions"][0].pop("secrets")
        self.assertEqual(result, original)

    def test_frontend_uses_its_own_path(self):
        result = self.render("frontend", [
            "/dameokja-v2-dev-fe/WEBHOOK", "/dameokja-v2-dev/BE_ONLY",
        ])
        self.assertEqual(result["containerDefinitions"][0]["secrets"], [
            {"name": "WEBHOOK", "valueFrom": "/dameokja-v2-dev-fe/WEBHOOK"}
        ])

    def test_nginx_and_redis_remain_unchanged_without_aws_calls(self):
        with patch.object(MODULE, "parameter_names") as names:
            for service in ("nginx", "redis"):
                task = self.template(service)
                self.assertEqual(MODULE.render(copy.deepcopy(task), service), task)
            names.assert_not_called()

    def test_empty_or_nested_only_results_stop_deployment(self):
        for names in ([], ["/dameokja-v2-dev/nginx/default.conf"]):
            with self.subTest(names=names), self.assertRaises(ValueError):
                self.render("backend", names)

    def test_invalid_environment_name_stops_deployment(self):
        with self.assertRaises(ValueError):
            self.render("backend", ["/dameokja-v2-dev/invalid-name"])

    def test_static_environment_collision_stops_deployment(self):
        with self.assertRaises(ValueError):
            self.render("backend", ["/dameokja-v2-dev/SERVER_TOMCAT_MBEANREGISTRY_ENABLED"])

    def test_selects_container_by_name_and_preserves_sidecar(self):
        task = self.template("backend")
        sidecar = {"name": "sidecar", "image": "example", "secrets": []}
        task["containerDefinitions"].insert(0, copy.deepcopy(sidecar))
        with patch.object(MODULE, "parameter_names", return_value=["/dameokja-v2-dev/KEY"]):
            result = MODULE.render(task, "backend")
        self.assertEqual(result["containerDefinitions"][0], sidecar)
        self.assertEqual(result["containerDefinitions"][1]["secrets"][0]["name"], "KEY")

    def test_missing_container_fails(self):
        with self.assertRaises(ValueError):
            MODULE.render(self.template("frontend"), "backend")

    def test_metadata_only_query_and_cli_automatic_pagination(self):
        names = [f"/dameokja-v2-dev/KEY_{i}" for i in range(120)]
        response = subprocess.CompletedProcess([], 0, json.dumps(names))
        with patch.object(MODULE.subprocess, "run", return_value=response) as run:
            self.assertEqual(MODULE.parameter_names("/dameokja-v2-dev/"), names)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["aws", "ssm", "describe-parameters"])
        filters = json.loads(command[command.index("--parameter-filters") + 1])
        self.assertEqual(filters, [{"Key": "Path", "Option": "OneLevel", "Values": ["/dameokja-v2-dev"]}])
        self.assertNotIn("--no-paginate", command)
        self.assertNotIn("--max-items", command)
        self.assertNotIn("--with-decryption", command)
        self.assertTrue(run.call_args.kwargs["check"])

    def test_aws_failure_does_not_overwrite_task_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "td.json"
            original = json.dumps(self.template("backend"))
            path.write_text(original, encoding="utf-8")
            args = ["render", "--service", "backend", "--input", str(path), "--output", str(path)]
            failure = subprocess.CalledProcessError(1, "aws")
            with patch.object(sys, "argv", args), patch.object(MODULE.subprocess, "run", side_effect=failure):
                with self.assertRaises(SystemExit) as result:
                    MODULE.main()
            self.assertEqual(result.exception.code, 1)
            self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
