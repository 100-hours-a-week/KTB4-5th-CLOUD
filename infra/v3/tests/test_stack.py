"""AWS 호출 없이 배포 대상 제한과 스택 입력 전달을 검증합니다."""

import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("v3_stack", ROOT / "scripts" / "stack.py")
stack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stack)
SHA = "a" * 40
ROLE = f"arn:aws:iam::{stack.ACCOUNT}:role/dameokja-cfn-execution"


class StackTests(unittest.TestCase):
    def change(self, **overrides):
        return {
            "StackName": "dameokja-v3-eks", "Description": f"v3:{SHA}:eks",
            "Status": "CREATE_COMPLETE", "ExecutionStatus": "AVAILABLE",
            "Changes": [{"ResourceChange": {"LogicalResourceId": "Cluster", "Action": "Add"}}],
            **overrides,
        }

    def test_existing_environment_names_are_rejected(self):
        for key in ("dev", "prod", "stage", "../../prod"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                stack.stack_name(key)

    def test_outputs_replace_all_downstream_placeholders(self):
        outputs = {key: f"output-{key}" for keys in stack.OUTPUT_KEYS.values() for key in keys}
        for key in stack.STACKS:
            with self.subTest(key=key):
                entries = stack.parameters(key, outputs)
                values = {item["ParameterKey"]: item["ParameterValue"] for item in entries}
                self.assertNotIn("REPLACE_", json.dumps(entries))
                for name in stack.OUTPUT_KEYS.get(key, ()):
                    self.assertEqual(values[name], outputs[name])
        self.assertEqual(stack.parameters("eks", {})[2]["ParameterValue"], "vpc-04fc5393afa0ce9fd")

    def test_missing_outputs_fail_instead_of_using_examples(self):
        for key in stack.OUTPUT_KEYS:
            with self.subTest(key=key), self.assertRaises(ValueError):
                stack.parameters(key, {})

    def test_only_available_change_from_same_commit_and_stack_is_accepted(self):
        stack.check_change_set(self.change(), "eks", SHA)
        for field, value in (
            ("StackName", "dameokja-prod"), ("Description", f"v3:{'b' * 40}:eks"),
            ("Status", "FAILED"), ("ExecutionStatus", "EXECUTE_COMPLETE"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                stack.check_change_set(self.change(**{field: value}), "eks", SHA)

    def test_delete_and_replacement_are_rejected(self):
        for resource in (
            {"Action": "Remove"}, {"Action": "Modify", "Replacement": "True"},
            {"Action": "Modify", "Replacement": "Conditional"},
        ):
            with self.subTest(resource=resource), self.assertRaises(ValueError):
                stack.check_change_set(self.change(Changes=[{"ResourceChange": resource}]), "eks", SHA)

    def test_access_denied_is_not_treated_as_new_stack(self):
        with patch.object(stack, "aws", side_effect=RuntimeError("AccessDenied")):
            with self.assertRaises(RuntimeError):
                stack.describe_stack("eks", allow_missing=True)
        with patch.object(stack, "aws", side_effect=RuntimeError("ValidationError: Stack does not exist")):
            self.assertIsNone(stack.describe_stack("eks", allow_missing=True))

    def test_non_manual_or_non_main_run_stops_before_aws(self):
        for environment in (
            {"GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "push"},
            {"GITHUB_REF": "refs/heads/feature", "GITHUB_EVENT_NAME": "workflow_dispatch"},
        ):
            with self.subTest(environment=environment), patch.dict(stack.os.environ, environment, clear=True):
                with patch("sys.argv", ["stack.py", "apply", "eks"]), patch.object(stack, "aws") as api:
                    with self.assertRaises(ValueError):
                        stack.main()
                    api.assert_not_called()

    def test_plan_creates_change_set_without_executing_it(self):
        arn = f"arn:aws:cloudformation:{stack.REGION}:{stack.ACCOUNT}:changeSet/v3-test/id"
        environment = {
            "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "V3_INFRA_ENABLED": "true", "GITHUB_SHA": SHA, "EXECUTION_ROLE": ROLE,
        }
        change = self.change(ChangeSetId=arn)

        def api(*args):
            if args[:2] == ("sts", "get-caller-identity"):
                return {"Account": stack.ACCOUNT}
            if args[:2] == ("cloudformation", "create-change-set"):
                return {"Id": arn}
            if args[:2] == ("cloudformation", "describe-change-set"):
                return change
            return {}

        with patch.dict(stack.os.environ, environment, clear=True), patch("sys.argv", ["stack.py", "plan", "eks"]):
            with patch.object(stack, "describe_stack", return_value=None), patch.object(stack, "aws", side_effect=api) as mock:
                with patch("builtins.print"):
                    stack.main()
                calls = [call.args[:2] for call in mock.call_args_list]
                self.assertIn(("cloudformation", "create-change-set"), calls)
                self.assertNotIn(("cloudformation", "execute-change-set"), calls)

    def test_apply_executes_only_the_reviewed_change_set(self):
        arn = f"arn:aws:cloudformation:{stack.REGION}:{stack.ACCOUNT}:changeSet/v3-test/id"
        environment = {
            "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "V3_INFRA_ENABLED": "true", "GITHUB_SHA": SHA, "EXECUTION_ROLE": ROLE,
        }

        def api(*args):
            if args[:2] == ("sts", "get-caller-identity"):
                return {"Account": stack.ACCOUNT}
            if args[:2] == ("cloudformation", "describe-change-set"):
                return self.change(ChangeSetId=arn)
            return {}

        with patch.dict(stack.os.environ, environment, clear=True):
            with patch("sys.argv", ["stack.py", "apply", "eks", "--change-set", arn]):
                with patch.object(stack, "describe_stack", return_value={"StackStatus": "REVIEW_IN_PROGRESS"}):
                    with patch.object(stack, "aws", side_effect=api) as mock, patch("builtins.print"):
                        stack.main()
                        mock.assert_any_call("cloudformation", "execute-change-set", "--change-set-name", arn)
                        mock.assert_any_call("cloudformation", "wait", "stack-create-complete", "--stack-name", "dameokja-v3-eks")
                        self.assertNotIn(("cloudformation", "create-change-set"), [c.args[:2] for c in mock.call_args_list])


if __name__ == "__main__":
    unittest.main()
