from contextlib import redirect_stdout
import io
import json
import os
import runpy
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
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

    def test_contacts_get_is_read_only_and_needs_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "contacts", "get", "contact123", cwd=tmp, env=clean_env(tmp)
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing GHL_PRIVATE_INTEGRATION_TOKEN", result.stderr)
        self.assertNotIn("dryRun", result.stdout)

    def test_contacts_create_defaults_to_guarded_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = clean_env(tmp)
            env["GHL_LOCATION_ID"] = "location123"
            result = self.run_cli(
                "contacts",
                "create",
                "--name",
                "Example Person",
                "--email",
                "person@example.invalid",
                cwd=tmp,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["dryRun"])
        self.assertEqual(payload["method"], "POST")
        self.assertEqual(payload["url"], "https://services.leadconnectorhq.com/contacts/")
        self.assertEqual(
            payload["body"],
            {
                "locationId": "location123",
                "name": "Example Person",
                "email": "person@example.invalid",
            },
        )

    def test_contacts_create_validates_phone_entries_before_env_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "contacts",
                "create",
                "--name",
                "Example Person",
                "--phone-entry",
                "Fax=+1 555-010-1001",
                cwd=tmp,
                env=clean_env(tmp),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid phone label", result.stderr)
        self.assertNotIn("missing GHL_LOCATION_ID", result.stderr)

    def test_contacts_update_serializes_ordered_phone_entries_and_additional_emails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "contacts",
                "update",
                "contact123",
                "--phone-entry",
                "Mobile=+1 555-010-1001",
                "--phone-entry",
                "Work=+1 555-010-1002",
                "--phone-entry",
                "Unlabeled=+1 555-010-1003",
                "--additional-email",
                "alternate@example.invalid",
                cwd=tmp,
                env=clean_env(tmp),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["body"]["additionalPhones"],
            [
                {"phone": "+1 555-010-1001", "label": "Mobile"},
                {"phone": "+1 555-010-1002", "label": "Work"},
                {"phone": "+1 555-010-1003"},
            ],
        )
        self.assertEqual(
            payload["body"]["additionalEmails"],
            [{"email": "alternate@example.invalid"}],
        )

    def test_contacts_update_rejects_invalid_phone_label_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "contacts",
                "update",
                "contact123",
                "--phone-entry",
                "Fax=+1 555-010-1001",
                cwd=tmp,
                env=clean_env(tmp),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid phone label", result.stderr)
        self.assertNotIn("missing GHL_PRIVATE_INTEGRATION_TOKEN", result.stderr)

    def test_contacts_update_rejects_mixed_phone_interfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "contacts",
                "update",
                "contact123",
                "--phone",
                "+1 555-010-1001",
                "--phone-entry",
                "Mobile=+1 555-010-1002",
                cwd=tmp,
                env=clean_env(tmp),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be combined", result.stderr)
        self.assertNotIn("missing GHL_PRIVATE_INTEGRATION_TOKEN", result.stderr)

    def test_contacts_update_rejects_equivalent_nanp_phone_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "contacts",
                "update",
                "contact123",
                "--phone-entry",
                "Mobile=(202) 555-0101",
                "--phone-entry",
                "Work=+1 202 555 0101",
                cwd=tmp,
                env=clean_env(tmp),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate phone", result.stderr)
        self.assertNotIn("missing GHL_PRIVATE_INTEGRATION_TOKEN", result.stderr)

    def test_contact_dnd_payload_preserves_untouched_channels(self):
        module = runpy.run_path(str(CLI))
        merge_dnd_settings = module["merge_dnd_settings"]
        existing = {
            "dndSettings": {
                "Email": {"status": "inactive", "message": "kept"},
                "SMS": {"status": "permanent"},
            }
        }

        result = merge_dnd_settings(existing, "Email", "active")

        self.assertEqual(
            result,
            {
                "Email": {"status": "active", "message": "kept"},
                "SMS": {"status": "permanent"},
            },
        )
        self.assertEqual(existing["dndSettings"]["Email"]["status"], "inactive")

    def test_contacts_dnd_rejects_invalid_channel_and_status_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = clean_env(tmp)
            bad_channel = self.run_cli(
                "contacts",
                "dnd",
                "contact123",
                "--channel",
                "Postal",
                "--status",
                "active",
                cwd=tmp,
                env=env,
            )
            bad_status = self.run_cli(
                "contacts",
                "dnd",
                "contact123",
                "--channel",
                "Email",
                "--status",
                "blocked",
                cwd=tmp,
                env=env,
            )

        self.assertNotEqual(bad_channel.returncode, 0)
        self.assertIn("invalid choice", bad_channel.stderr)
        self.assertNotIn("missing GHL_PRIVATE_INTEGRATION_TOKEN", bad_channel.stderr)
        self.assertNotEqual(bad_status.returncode, 0)
        self.assertIn("invalid choice", bad_status.stderr)
        self.assertNotIn("missing GHL_PRIVATE_INTEGRATION_TOKEN", bad_status.stderr)

    def test_contact_subcommands_are_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = clean_env(tmp)
            for command in ("get", "create", "update", "dnd"):
                with self.subTest(command=command):
                    result = self.run_cli(
                        "contacts", command, "--help", cwd=tmp, env=env
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_task_search_pagination_stops_after_one_short_page(self):
        module = runpy.run_path(str(CLI))
        paginate = module["paginate_task_search"]
        calls = []

        def fetch_page(body):
            calls.append(body)
            return {"tasks": [{"_id": f"task-{index}"} for index in range(3)]}

        result = paginate(100, fetch_page)

        self.assertEqual(len(result["tasks"]), 3)
        self.assertEqual(calls, [{"limit": 20}])

    def test_task_search_handler_aggregates_filters_and_preserves_request_contract(self):
        module = runpy.run_path(str(CLI))
        calls = []
        pages = [
            {
                "tasks": [
                    {
                        "_id": f"task-{index}",
                        "contactId": None if index == 0 else f"contact-{index}",
                        "searchAfter": [1000 + index, f"task-{index}"],
                    }
                    for index in range(20)
                ],
                "traceId": "first-page",
            },
            {
                "tasks": [
                    {
                        "_id": "task-20",
                        "contactId": None,
                        "searchAfter": [1020, "task-20"],
                    }
                ]
            },
        ]

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return pages.pop(0)

        handler_globals = module["search_tasks"].__globals__
        handler_globals["location_id"] = lambda: "location123"
        handler_globals["request"] = fake_request
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            module["search_tasks"](
                SimpleNamespace(limit=100, exclude_unlinked=True)
            )

        result = json.loads(stdout.getvalue())
        self.assertEqual(len(result["tasks"]), 19)
        self.assertEqual(result["unlinkedTaskCount"], 2)
        self.assertEqual(result["traceId"], "first-page")
        self.assertEqual(
            calls[0],
            (
                "POST",
                "/locations/location123/tasks/search",
                {
                    "body": {"limit": 20},
                    "mutates": False,
                    "version": "v3",
                },
            ),
        )
        self.assertEqual(
            calls[1][2]["body"],
            {"limit": 20, "searchAfter": [1019, "task-19"]},
        )

    def test_task_search_pagination_preserves_empty_first_page_structure(self):
        module = runpy.run_path(str(CLI))
        paginate = module["paginate_task_search"]

        result = paginate(100, lambda _body: {"tasks": [], "traceId": "empty"})

        self.assertEqual(result, {"tasks": [], "traceId": "empty"})

    def test_task_search_pagination_rejects_nonpositive_limit(self):
        module = runpy.run_path(str(CLI))
        paginate = module["paginate_task_search"]
        pagination_error = module["TaskPaginationError"]

        for limit in (0, -1):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(pagination_error, "greater than zero"):
                    paginate(limit, lambda _body: {"tasks": []})

    def test_task_search_pagination_fetches_empty_page_after_exactly_twenty(self):
        module = runpy.run_path(str(CLI))
        paginate = module["paginate_task_search"]
        cursor = [1000, "task-19"]
        pages = [
            {
                "tasks": [
                    {"_id": f"task-{index}", "searchAfter": [1000, f"task-{index}"]}
                    for index in range(20)
                ],
                "traceId": "first-page",
            },
            {"tasks": [], "traceId": "second-page"},
        ]
        calls = []

        def fetch_page(body):
            calls.append(body)
            return pages.pop(0)

        result = paginate(100, fetch_page)

        self.assertEqual(len(result["tasks"]), 20)
        self.assertEqual(result["traceId"], "first-page")
        self.assertEqual(calls, [{"limit": 20}, {"limit": 20, "searchAfter": cursor}])

    def test_task_search_pagination_combines_multiple_pages(self):
        module = runpy.run_path(str(CLI))
        paginate = module["paginate_task_search"]
        pages = []
        for start, count in ((0, 20), (20, 20), (40, 5)):
            pages.append(
                {
                    "tasks": [
                        {
                            "_id": f"task-{index}",
                            "searchAfter": [1000 + index, f"task-{index}"],
                        }
                        for index in range(start, start + count)
                    ]
                }
            )
        calls = []

        def fetch_page(body):
            calls.append(body)
            return pages.pop(0)

        result = paginate(100, fetch_page)

        self.assertEqual(len(result["tasks"]), 45)
        self.assertEqual(result["tasks"][20]["_id"], "task-20")
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1]["searchAfter"], [1019, "task-19"])
        self.assertEqual(calls[2]["searchAfter"], [1039, "task-39"])

    def test_task_search_pagination_truncates_at_requested_limit(self):
        module = runpy.run_path(str(CLI))
        paginate = module["paginate_task_search"]
        calls = []

        def fetch_page(body):
            calls.append(body)
            start = 0 if len(calls) == 1 else 20
            return {
                "tasks": [
                    {
                        "_id": f"task-{index}",
                        "searchAfter": [1000 + index, f"task-{index}"],
                    }
                    for index in range(start, start + 20)
                ]
            }

        result = paginate(25, fetch_page)

        self.assertEqual(len(result["tasks"]), 25)
        self.assertEqual(result["tasks"][-1]["_id"], "task-24")
        self.assertEqual(calls[1]["limit"], 5)
        self.assertEqual(len(calls), 2)

    def test_task_search_pagination_rejects_malformed_cursor(self):
        module = runpy.run_path(str(CLI))
        paginate = module["paginate_task_search"]
        pagination_error = module["TaskPaginationError"]

        def fetch_page(_body):
            return {
                "tasks": [
                    {"_id": f"task-{index}", "searchAfter": "not-a-cursor"}
                    for index in range(20)
                ]
            }

        with self.assertRaisesRegex(pagination_error, "malformed searchAfter cursor"):
            paginate(100, fetch_page)

    def test_task_search_pagination_rejects_missing_cursor(self):
        module = runpy.run_path(str(CLI))
        paginate = module["paginate_task_search"]
        pagination_error = module["TaskPaginationError"]

        def fetch_page(_body):
            return {"tasks": [{"_id": f"task-{index}"} for index in range(20)]}

        with self.assertRaisesRegex(pagination_error, "missing a searchAfter cursor"):
            paginate(100, fetch_page)

    def test_task_search_pagination_rejects_repeated_cursor(self):
        module = runpy.run_path(str(CLI))
        paginate = module["paginate_task_search"]
        pagination_error = module["TaskPaginationError"]
        cursor = [1000, "task-boundary"]
        call_count = 0

        def fetch_page(_body):
            nonlocal call_count
            call_count += 1
            return {
                "tasks": [
                    {"_id": f"task-{call_count}-{index}", "searchAfter": cursor}
                    for index in range(20)
                ]
            }

        with self.assertRaisesRegex(pagination_error, "repeated its searchAfter cursor"):
            paginate(100, fetch_page)
        self.assertEqual(call_count, 2)

    def test_task_search_pagination_surfaces_later_page_failure(self):
        module = runpy.run_path(str(CLI))
        paginate = module["paginate_task_search"]
        calls = 0

        def fetch_page(_body):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic API failure")
            return {
                "tasks": [
                    {"_id": f"task-{index}", "searchAfter": [1000, f"task-{index}"]}
                    for index in range(20)
                ]
            }

        with self.assertRaisesRegex(RuntimeError, "synthetic API failure"):
            paginate(100, fetch_page)
        self.assertEqual(calls, 2)

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
