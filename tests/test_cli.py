from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlparse


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

    def run_cli_with_provider(self, responder, *args):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                query = {
                    key: values[0] if len(values) == 1 else values
                    for key, values in parse_qs(
                        parsed.query, keep_blank_values=True
                    ).items()
                }
                requests.append((parsed.path, query))
                response = responder(parsed.path, query)
                if isinstance(response, tuple):
                    status, payload = response
                else:
                    status, payload = 200, response
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = clean_env(tmp)
                env["GHL_LOCATION_ID"] = "synthetic-location"
                env["GHL_PRIVATE_INTEGRATION_TOKEN"] = "synthetic-token"
                runner = """
import runpy
import sys

cli_path = sys.argv.pop(1)
base_url = sys.argv.pop(1)
module = runpy.run_path(cli_path)
module["request"].__globals__["BASE_URL"] = base_url
module["main"]()
"""
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        runner,
                        str(CLI),
                        f"http://127.0.0.1:{server.server_port}",
                        *args,
                    ],
                    cwd=tmp,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        return result, requests

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
        self.assertIn(
            'opportunities search --pipeline "Sales Pipeline" --status open --all',
            result.stdout,
        )
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

    def test_opportunity_search_all_stops_after_one_short_page(self):
        page = {
            "opportunities": [{"id": "opp-1"}, {"id": "opp-2"}],
            "meta": {"total": 2},
            "aggregations": {"status": {"open": 2}},
        }

        result, requests = self.run_cli_with_provider(
            lambda _path, _query: page,
            "opportunities",
            "search",
            "--all",
            "--query",
            "Example Co",
            "--status",
            "open",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), page)
        self.assertEqual(
            requests,
            [
                (
                    "/opportunities/search",
                    {
                        "location_id": "synthetic-location",
                        "limit": "100",
                        "status": "open",
                        "q": "Example Co",
                    },
                )
            ],
        )

    def test_opportunity_search_all_combines_pages_and_reapplies_filters(self):
        opportunity_calls = 0

        def responder(path, query):
            nonlocal opportunity_calls
            if path == "/opportunities/pipelines":
                return {
                    "pipelines": [
                        {"id": "pipeline-123", "name": "Sales Pipeline"}
                    ]
                }
            opportunity_calls += 1
            if opportunity_calls == 1:
                return {
                    "opportunities": [
                        {"id": f"opp-{index}"} for index in range(100)
                    ],
                    "meta": {
                        "total": 102,
                        "currentPage": 1,
                        "nextPage": 2,
                        "startAfter": 1720000000000,
                        "startAfterId": "opp-99",
                    },
                    "aggregations": {"status": {"open": 102}},
                }
            return {
                "opportunities": [{"id": "opp-100"}, {"id": "opp-101"}],
                "meta": {"total": 102, "currentPage": 2},
            }

        result, requests = self.run_cli_with_provider(
            responder,
            "opportunities",
            "search",
            "--all",
            "--query",
            "Example Co",
            "--pipeline",
            "Sales Pipeline",
            "--status",
            "open",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["opportunities"]), 102)
        self.assertEqual(payload["opportunities"][-1]["id"], "opp-101")
        self.assertEqual(payload["meta"], {"total": 102, "currentPage": 2})
        self.assertEqual(payload["aggregations"], {"status": {"open": 102}})
        page_queries = [query for path, query in requests if path == "/opportunities/search"]
        self.assertEqual(len(page_queries), 2)
        expected_filters = {
            "location_id": "synthetic-location",
            "limit": "100",
            "status": "open",
            "q": "Example Co",
            "pipeline_id": "pipeline-123",
        }
        self.assertEqual(page_queries[0], expected_filters)
        self.assertEqual(
            page_queries[1],
            {
                **expected_filters,
                "startAfter": "1720000000000",
                "startAfterId": "opp-99",
            },
        )

    def test_opportunity_search_all_fetches_after_exactly_full_page(self):
        pages = [
            {
                "opportunities": [
                    {"id": f"opp-{index}"} for index in range(100)
                ],
                "meta": {
                    "total": 100,
                    "startAfter": 1720000000000,
                    "startAfterId": "opp-99",
                },
            },
            {"opportunities": [], "meta": {"total": 100}},
        ]

        result, requests = self.run_cli_with_provider(
            lambda _path, _query: pages.pop(0),
            "opportunities",
            "search",
            "--all",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["opportunities"]), 100)
        self.assertEqual(len(requests), 2)

    def test_opportunity_search_all_continues_after_short_nonterminal_page(self):
        pages = [
            {
                "opportunities": [{"id": "opp-1"}, {"id": "opp-2"}],
                "meta": {
                    "total": 3,
                    "currentPage": 1,
                    "nextPage": 2,
                    "startAfter": 1720000000000,
                    "startAfterId": "opp-2",
                },
            },
            {
                "opportunities": [{"id": "opp-3"}],
                "meta": {"total": 3, "currentPage": 2},
            },
        ]

        result, requests = self.run_cli_with_provider(
            lambda _path, _query: pages.pop(0),
            "opportunities",
            "search",
            "--all",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            [opportunity["id"] for opportunity in payload["opportunities"]],
            ["opp-1", "opp-2", "opp-3"],
        )
        self.assertEqual(payload["meta"], {"total": 3, "currentPage": 2})
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1][1]["startAfter"], "1720000000000")
        self.assertEqual(requests[1][1]["startAfterId"], "opp-2")

    def test_opportunity_search_all_continues_for_next_page_url_on_fixed_endpoint(self):
        pages = [
            {
                "opportunities": [{"id": "opp-1"}, {"id": "opp-2"}],
                "meta": {
                    "total": 3,
                    "currentPage": 1,
                    "nextPageUrl": "https://untrusted.invalid/do-not-follow",
                    "startAfter": 1720000000000,
                    "startAfterId": "opp-2",
                },
                "aggregations": {"status": {"open": 3}},
            },
            {
                "opportunities": [{"id": "opp-3"}],
                "meta": {"total": 3, "currentPage": 2},
            },
        ]

        result, requests = self.run_cli_with_provider(
            lambda _path, _query: pages.pop(0),
            "opportunities",
            "search",
            "--all",
            "--query",
            "Example Co",
            "--status",
            "open",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            [opportunity["id"] for opportunity in payload["opportunities"]],
            ["opp-1", "opp-2", "opp-3"],
        )
        self.assertEqual(payload["meta"], {"total": 3, "currentPage": 2})
        self.assertEqual(payload["aggregations"], {"status": {"open": 3}})
        self.assertEqual(
            requests,
            [
                (
                    "/opportunities/search",
                    {
                        "location_id": "synthetic-location",
                        "limit": "100",
                        "status": "open",
                        "q": "Example Co",
                    },
                ),
                (
                    "/opportunities/search",
                    {
                        "location_id": "synthetic-location",
                        "limit": "100",
                        "status": "open",
                        "q": "Example Co",
                        "startAfter": "1720000000000",
                        "startAfterId": "opp-2",
                    },
                ),
            ],
        )

    def test_opportunity_search_all_rejects_malformed_next_page_url(self):
        for next_page_url in ("", "   ", 2):
            with self.subTest(next_page_url=next_page_url):
                page = {
                    "opportunities": [],
                    "meta": {"total": 0, "nextPageUrl": next_page_url},
                }
                result, requests = self.run_cli_with_provider(
                    lambda _path, _query, page=page: page,
                    "opportunities",
                    "search",
                    "--all",
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("malformed nextPageUrl value", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(len(requests), 1)

    def test_opportunity_search_all_rejects_terminal_total_mismatch(self):
        page = {
            "opportunities": [{"id": "opp-1"}, {"id": "opp-2"}],
            "meta": {"total": 3, "currentPage": 1},
        }

        result, requests = self.run_cli_with_provider(
            lambda _path, _query: page,
            "opportunities",
            "search",
            "--all",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match pagination total 3", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(requests), 1)

    def test_opportunity_search_all_rejects_malformed_total(self):
        page = {
            "opportunities": [],
            "meta": {"total": "not-a-count"},
        }

        result, requests = self.run_cli_with_provider(
            lambda _path, _query: page,
            "opportunities",
            "search",
            "--all",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("malformed pagination total", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(requests), 1)

    def test_opportunity_search_all_rejects_changed_total(self):
        pages = [
            {
                "opportunities": [
                    {"id": f"opp-{index}"} for index in range(100)
                ],
                "meta": {
                    "total": 101,
                    "nextPage": 2,
                    "startAfter": 1720000000000,
                    "startAfterId": "opp-99",
                },
            },
            {
                "opportunities": [{"id": "opp-100"}],
                "meta": {"total": 102},
            },
        ]

        result, requests = self.run_cli_with_provider(
            lambda _path, _query: pages.pop(0),
            "opportunities",
            "search",
            "--all",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed pagination total from 101 to 102", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(requests), 2)

    def test_opportunity_search_all_rejects_missing_or_malformed_cursor(self):
        invalid_meta = [
            ({"startAfterId": "opp-99"}, "missing pagination cursor"),
            (
                {"startAfter": "not-a-timestamp", "startAfterId": "opp-99"},
                "malformed pagination cursor",
            ),
            (
                {"startAfter": 1720000000000, "startAfterId": ""},
                "malformed pagination cursor",
            ),
        ]

        for meta, expected_error in invalid_meta:
            with self.subTest(meta=meta):
                page = {
                    "opportunities": [
                        {"id": f"opp-{index}"} for index in range(100)
                    ],
                    "meta": meta,
                }
                result, requests = self.run_cli_with_provider(
                    lambda _path, _query, page=page: page,
                    "opportunities",
                    "search",
                    "--all",
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(len(requests), 1)

    def test_opportunity_search_all_rejects_repeated_cursor(self):
        page_number = 0
        cursor = {
            "startAfter": 1720000000000,
            "startAfterId": "opp-boundary",
        }

        def responder(_path, _query):
            nonlocal page_number
            page_number += 1
            return {
                "opportunities": [
                    {"id": f"opp-{page_number}-{index}"} for index in range(100)
                ],
                "meta": cursor,
            }

        result, requests = self.run_cli_with_provider(
            responder,
            "opportunities",
            "search",
            "--all",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repeated its pagination cursor", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(requests), 2)

    def test_opportunity_search_all_later_page_failure_emits_no_partial_result(self):
        request_number = 0

        def responder(_path, _query):
            nonlocal request_number
            request_number += 1
            if request_number == 2:
                return 503, {"message": "synthetic later-page failure"}
            return {
                "opportunities": [
                    {"id": f"opp-{index}"} for index in range(100)
                ],
                "meta": {
                    "startAfter": 1720000000000,
                    "startAfterId": "opp-99",
                },
            }

        result, requests = self.run_cli_with_provider(
            responder,
            "opportunities",
            "search",
            "--all",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HTTP 503", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(requests), 2)

    def test_opportunity_search_limit_remains_a_single_bounded_request(self):
        page = {
            "opportunities": [{"id": f"opp-{index}"} for index in range(7)],
            "meta": {"total": 20},
        }

        result, requests = self.run_cli_with_provider(
            lambda _path, _query: page,
            "opportunities",
            "search",
            "--limit",
            "7",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), page)
        self.assertEqual(
            requests,
            [
                (
                    "/opportunities/search",
                    {
                        "location_id": "synthetic-location",
                        "limit": "7",
                        "status": "all",
                    },
                )
            ],
        )

    def test_opportunity_search_rejects_all_with_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "opportunities",
                "search",
                "--all",
                "--limit",
                "10",
                cwd=tmp,
                env=clean_env(tmp),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not allowed with argument", result.stderr)

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
