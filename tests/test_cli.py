import os
import runpy
import subprocess
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "ghl"


def clean_env(tmpdir):
    env = os.environ.copy()
    env["GHL_ENV_FILE"] = str(Path(tmpdir) / "missing.env")
    env["GHL_CLI_CONFIG"] = str(Path(tmpdir) / "missing-config.json")
    env.pop("GHL_LOCATION_ID", None)
    env.pop("GHL_PRIVATE_INTEGRATION_TOKEN", None)
    return env


class GhlCliTests(unittest.TestCase):
    def run_cli(self, *args, cwd=None, env=None):
        return subprocess.run(
            [str(CLI), *args],
            cwd=cwd or ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_version_and_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = clean_env(tmp)
            version = self.run_cli("--version", env=env)
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn("ghl 0.3.0", version.stdout)

            help_result = self.run_cli("--help", env=env)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("doctor", help_result.stdout)

    def test_agent_help_uses_generic_defaults_without_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("help", "agent", cwd=tmp, env=clean_env(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('opportunities search --pipeline "Sales Pipeline"', result.stdout)
        self.assertIn("Deletes require global --yes and --confirm-delete ID", result.stdout)

    def test_init_creates_config_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "init",
                "--default-pipeline",
                "Custom Pipeline",
                "--stages",
                "Lead,Proposal,Closed",
                "--tags",
                "customer,prospect",
                "--example-company",
                "Acme Co",
                "--example-contact",
                "Alex Smith",
                "--example-email",
                "alex@example.com",
                cwd=tmp,
                env=clean_env(tmp),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(tmp) / ".ghl-cli.json").exists())
            self.assertFalse((Path(tmp) / ".env.local.example").exists())
            self.assertIn("Copy .env.example to .env", result.stdout)

    def test_write_defaults_to_dry_run_without_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("contacts", "delete", "contact123", cwd=tmp, env=clean_env(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"dryRun": true', result.stdout)
        self.assertIn("/contacts/contact123", result.stdout)

    def test_conversations_search_is_a_read_that_needs_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("conversations", "search", cwd=tmp, env=clean_env(tmp))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing GHL_LOCATION_ID", result.stderr)
        self.assertNotIn("dryRun", result.stdout)

    def test_tasks_search_is_a_read_that_needs_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("tasks", "search", cwd=tmp, env=clean_env(tmp))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing GHL_LOCATION_ID", result.stderr)
        self.assertNotIn("dryRun", result.stdout)

    def test_task_search_reports_unlinked_records_without_hiding_them(self):
        module = runpy.run_path(str(CLI))
        filter_result = module["filter_task_search_result"]
        data = {
            "tasks": [
                {"_id": "active", "contactId": "contact123"},
                {"_id": "orphaned", "contactId": None},
                {"_id": "unknown"},
            ]
        }

        result = filter_result(data)

        self.assertEqual(
            [task["_id"] for task in result["tasks"]],
            ["active", "orphaned", "unknown"],
        )
        self.assertEqual(result["unlinkedTaskCount"], 2)

    def test_task_search_can_exclude_unlinked_records(self):
        module = runpy.run_path(str(CLI))
        filter_result = module["filter_task_search_result"]
        data = {"tasks": [{"_id": "orphaned", "contactId": None}]}

        result = filter_result(data, exclude_unlinked=True)

        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["unlinkedTaskCount"], 1)

    def test_tasks_create_defaults_to_guarded_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "tasks",
                "create",
                "--contact-id",
                "contact123",
                "--title",
                "Follow up",
                "--body",
                "Dropped off case study.",
                "--due",
                "2026-07-14T09:00:00-04:00",
                "--assigned-to",
                "user123",
                cwd=tmp,
                env=clean_env(tmp),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"dryRun": true', result.stdout)
        self.assertIn('"url": "https://services.leadconnectorhq.com/contacts/contact123/tasks"', result.stdout)
        self.assertIn('"assignedTo": "user123"', result.stdout)
        self.assertIn('"dueDate": "2026-07-14T09:00:00-04:00"', result.stdout)
        self.assertIn('"completed": false', result.stdout)

    def test_tasks_create_rejects_due_without_timezone_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "tasks",
                "create",
                "--contact-id",
                "contact123",
                "--title",
                "Follow up",
                "--due",
                "2026-07-14T09:00:00",
                "--assigned-to",
                "user123",
                cwd=tmp,
                env=clean_env(tmp),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ISO 8601 datetime with timezone", result.stderr)
        self.assertNotIn("missing GHL_PRIVATE_INTEGRATION_TOKEN", result.stderr)

    def test_tasks_update_defaults_to_guarded_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "tasks",
                "update",
                "task123",
                "--contact-id",
                "contact123",
                "--due",
                "2026-07-20T09:00:00-04:00",
                cwd=tmp,
                env=clean_env(tmp),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"dryRun": true', result.stdout)
        self.assertIn('"url": "https://services.leadconnectorhq.com/contacts/contact123/tasks/task123"', result.stdout)
        self.assertIn('"dueDate": "2026-07-20T09:00:00-04:00"', result.stdout)

    def test_tasks_update_requires_a_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "tasks",
                "update",
                "task123",
                "--contact-id",
                "contact123",
                cwd=tmp,
                env=clean_env(tmp),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing to update", result.stderr)

    def test_tasks_delete_defaults_to_guarded_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "tasks",
                "delete",
                "task123",
                "--contact-id",
                "contact123",
                "--confirm-delete",
                "task123",
                cwd=tmp,
                env=clean_env(tmp),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"dryRun": true', result.stdout)
        self.assertIn(
            '"url": "https://services.leadconnectorhq.com/contacts/contact123/tasks/task123"',
            result.stdout,
        )

    def test_tasks_complete_defaults_to_guarded_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "tasks",
                "complete",
                "task123",
                "--contact-id",
                "contact123",
                cwd=tmp,
                env=clean_env(tmp),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"dryRun": true', result.stdout)
        self.assertIn(
            '"url": "https://services.leadconnectorhq.com/contacts/contact123/tasks/task123/completed"',
            result.stdout,
        )
        self.assertIn('"completed": true', result.stdout)

    def test_conversations_messages_is_a_read_that_needs_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "conversations", "messages", "conversation123", cwd=tmp, env=clean_env(tmp)
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing GHL_PRIVATE_INTEGRATION_TOKEN", result.stderr)
        self.assertNotIn("dryRun", result.stdout)

    def test_conversations_has_no_write_subcommands(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = clean_env(tmp)
            for write_command in ["send", "create", "update", "delete", "upsert"]:
                with self.subTest(write_command=write_command):
                    result = self.run_cli(
                        "--yes", "conversations", write_command, "x123", cwd=tmp, env=env
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("invalid choice", result.stderr)

    def test_help_agent_includes_read_only_conversations_recipes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("help", "agent", cwd=tmp, env=clean_env(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("conversations search", result.stdout)
        self.assertIn("conversations messages", result.stdout)
        self.assertIn("Conversations are read-only", result.stdout)

    def test_help_agent_includes_guarded_tasks_recipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("help", "agent", cwd=tmp, env=clean_env(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tasks search --limit 100", result.stdout)
        self.assertIn("tasks create", result.stdout)
        self.assertIn("tasks complete", result.stdout)
        self.assertIn("tasks delete", result.stdout)

    def test_next_step_cannot_touch_stage_status_or_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = clean_env(tmp)
            for forbidden in ["--stage", "--status", "--value"]:
                with self.subTest(forbidden=forbidden):
                    result = self.run_cli(
                        "opportunities", "next-step", "opp123", forbidden, "x",
                        cwd=tmp, env=env,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unrecognized arguments", result.stderr)

    def test_next_step_validates_date_format_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "opportunities", "next-step", "opp123", "--date", "07/10/2026",
                cwd=tmp, env=clean_env(tmp),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("YYYY-MM-DD", result.stderr)
        self.assertNotIn("missing GHL_LOCATION_ID", result.stderr)

    def test_next_step_requires_step_or_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "opportunities", "next-step", "opp123", cwd=tmp, env=clean_env(tmp)
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing to update", result.stderr)

    def test_help_agent_includes_next_step_recipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("help", "agent", cwd=tmp, env=clean_env(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("opportunities next-step", result.stdout)

    def test_delete_requires_matching_confirmation_with_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = clean_env(tmp)
            cases = [
                ("contacts", "delete", "contact123"),
                ("opportunities", "delete", "opportunity123"),
                ("tags", "delete", "tag123"),
            ]
            for command in cases:
                with self.subTest(command=command):
                    missing = self.run_cli("--yes", *command, cwd=tmp, env=env)
                    wrong = self.run_cli(
                        "--yes",
                        *command,
                        "--confirm-delete",
                        "other",
                        cwd=tmp,
                        env=env,
                    )

                    self.assertNotEqual(missing.returncode, 0)
                    self.assertIn("refusing delete", missing.stderr)
                    self.assertNotIn("missing GHL_PRIVATE_INTEGRATION_TOKEN", missing.stderr)

                    self.assertNotEqual(wrong.returncode, 0)
                    self.assertIn("refusing delete", wrong.stderr)
                    self.assertNotIn("missing GHL_PRIVATE_INTEGRATION_TOKEN", wrong.stderr)

    def test_task_delete_requires_matching_confirmation_with_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = clean_env(tmp)
            command = (
                "--yes",
                "tasks",
                "delete",
                "task123",
                "--contact-id",
                "contact123",
            )
            missing = self.run_cli(*command, cwd=tmp, env=env)
            wrong = self.run_cli(
                *command,
                "--confirm-delete",
                "other",
                cwd=tmp,
                env=env,
            )

        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("refusing delete", missing.stderr)
        self.assertNotIn("missing GHL_PRIVATE_INTEGRATION_TOKEN", missing.stderr)

        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn("refusing delete", wrong.stderr)
        self.assertNotIn("missing GHL_PRIVATE_INTEGRATION_TOKEN", wrong.stderr)


if __name__ == "__main__":
    unittest.main()
