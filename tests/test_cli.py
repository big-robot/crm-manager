from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import runpy
import subprocess
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
import urllib.parse
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


class SyntheticGhlServer:
    def __init__(self, callback):
        self.callback = callback
        self.requests = []

        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def do_PUT(self):
                self.handle_request()

            def do_DELETE(self):
                self.handle_request()

            def handle_request(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length) if length else b""
                body = json.loads(raw_body) if raw_body else None
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                request = {
                    "method": self.command,
                    "path": parsed.path,
                    "query": query,
                    "body": body,
                    "version": self.headers.get("Version"),
                }
                owner.requests.append(request)
                response = owner.callback(request)
                status, payload = response[:2]
                response_headers = response[2] if len(response) == 3 else {}
                encoded = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                for name, value in response_headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class GhlCliTests(unittest.TestCase):
    def test_businesses_create_update(self):
        fields = {
            "name": "Synthetic Business", "phone": "+15550101001", "email": "business@example.invalid",
            "website": "https://example.invalid", "address": "1 Synthetic Street", "city": "Synthetic City",
            "postal_code": "00000", "state": "Synthetic State", "country": "US", "description": "Synthetic description",
        }
        cli = runpy.run_path(str(CLI))
        self.assertEqual(set(fields), set(cli["BUSINESS_FIELDS"]))
        expected = {key if key != "postal_code" else "postalCode": value for key, value in fields.items()}
        business = {"id": "business-synthetic", "locationId": "location-synthetic", **expected}
        def respond(request):
            if request["method"] != "GET":
                business.update(request["body"])
                return 200, {"success": True, "business": business.copy()}
            return 200, {"business": business.copy()}
        flags = [item for key, value in fields.items() for item in ("--" + key.replace("_", "-"), value)]
        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            env = self.synthetic_env(tmp, server)
            for command in ("create", "update"):
                with self.subTest(command=command):
                    command_args = ["businesses", command] + ([business["id"]] if command == "update" else [])
                    result = self.run_cli("--yes", *command_args, *flags, cwd=tmp, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), {"success": True, "business": business})
                    request = server.requests[-1]
                    self.assertEqual(request["method"], "POST" if command == "create" else "PUT")
                    self.assertEqual(request["path"], "/businesses/" if command == "create" else "/businesses/business-synthetic")
                    self.assertEqual(request["body"], {**expected, **({"locationId": "location-synthetic"} if command == "create" else {})})
                    empty_flags = [item for key in fields if key != "name" for item in ("--" + key.replace("_", "-"), " ")]
                    result = self.run_cli("--yes", *command_args, "--name", "Synthetic Business", *empty_flags, cwd=tmp, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(server.requests[-1]["body"], {"name": "Synthetic Business", **({"locationId": "location-synthetic"} if command == "create" else {})})
            result = self.run_cli("--yes", "businesses", "update", business["id"], "--name", "Renamed Synthetic Business", "--phone", "  ", cwd=tmp, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(server.requests[-1]["body"], {"name": "Renamed Synthetic Business"})
            self.assertEqual(json.loads(result.stdout)["business"]["description"], fields["description"])
            result = self.run_cli("--yes", "businesses", "create", "--name", "Synthetic Business", cwd=tmp, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(server.requests[-1]["body"], {"name": "Synthetic Business", "locationId": "location-synthetic"})
            self.assertTrue(all(request["version"] == "v3" for request in server.requests))

    def test_businesses_mutation_guards(self):
        business = {"id": "business-synthetic", "locationId": "location-synthetic", "name": "Synthetic Business"}
        def respond(request):
            return 200, {"business": business} if request["method"] == "GET" else {"success": True}
        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            env = self.synthetic_env(tmp, server)
            commands = {"create": ["--name", business["name"]], "update": [business["id"], "--name", "Renamed"], "delete": [business["id"]]}
            for command, flags in commands.items():
                result = self.run_cli("businesses", command, *flags, cwd=tmp, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout)["dryRun"])
                self.assertTrue(all(request["method"] == "GET" for request in server.requests))
            for flags in ([], ["--confirm-delete", "business-other"]):
                before = len(server.requests)
                result = self.run_cli("--yes", "businesses", "delete", business["id"], *flags, cwd=tmp, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(server.requests), before)
            result = self.run_cli("--yes", "businesses", "delete", business["id"], "--confirm-delete", business["id"], cwd=tmp, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"success": True})
            writes = [request for request in server.requests if request["method"] != "GET"]
            self.assertEqual([(request["method"], request["path"], request["body"], request["version"]) for request in writes], [("DELETE", "/businesses/business-synthetic", None, "v3")])

    def test_businesses_invalid_inputs(self):
        commands = [
            ["list", "--limit", "0"], ["list", "--limit", "-1"], ["list", "--skip", "-1"],
            ["create"], ["create", "--name", " "], ["update", "business-synthetic"],
            ["update", "business-synthetic", "--description", " "], ["update", "business-synthetic", "--name", ""],
        ]
        for bad_id in ("../business-synthetic", "business?id=1", "business#1", "", "x" * 257):
            commands.extend([["get", bad_id], ["update", bad_id, "--name", "Synthetic Business"], ["delete", bad_id, "--confirm-delete", bad_id]])
        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(lambda request: (500, {})) as server:
            for flags in commands:
                with self.subTest(flags=flags):
                    result = self.run_cli("--yes", "businesses", *flags, cwd=tmp, env=self.synthetic_env(tmp, server))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(server.requests, [])

    def test_businesses_preflight_provider_failures(self):
        business = {"id": "business-synthetic", "locationId": "location-synthetic", "name": "Synthetic Business"}
        bad_businesses = [None, [], {}, {**business, "id": "business-other"}, {**business, "id": "../invalid"}, {**business, "locationId": "location-other"}, {**business, "name": " "}]
        for key in ("id", "locationId", "name"):
            bad_businesses.append({field: value for field, value in business.items() if field != key})
        failures = [(code, {"private": "PRIVATE_PROVIDER_ERROR"}) for code in (403, 404, 429)]
        failures += [(200, payload) for payload in (b"PRIVATE_PROVIDER_ERROR", b"\xff", [], {}, {"business": None})]
        failures += [(200, {"business": value}) for value in bad_businesses]
        state = {"response": None}
        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(lambda request: state["response"]) as server:
            for response in failures:
                state["response"] = response
                for command in (["get", business["id"]], ["update", business["id"], "--name", "Renamed"], ["delete", business["id"], "--confirm-delete", business["id"]]):
                    with self.subTest(response=response, command=command):
                        server.requests.clear()
                        result = self.run_cli("--yes", "businesses", *command, cwd=tmp, env=self.synthetic_env(tmp, server))
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, "")
                        self.assertNotIn("PRIVATE_PROVIDER_ERROR", result.stderr)
                        self.assertNotIn("token-synthetic", result.stderr)
                        self.assertEqual([request["method"] for request in server.requests], ["GET"])

    def test_businesses_list_provider_failures(self):
        good = {"id": "business-synthetic", "locationId": "location-synthetic", "name": "Synthetic Business"}
        failures = [(code, {"private": "PRIVATE_PROVIDER_ERROR"}) for code in (403, 429)]
        failures += [(200, payload) for payload in (b"PRIVATE_PROVIDER_ERROR", [], {}, {"businesses": {}}, {"businesses": [good, None]}, {"businesses": [good, {**good, "locationId": "location-other"}]}, {"businesses": [{"name": "Synthetic Business"}]})]
        state = {"response": None}
        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(lambda request: state["response"]) as server:
            for response in failures:
                state["response"] = response
                server.requests.clear()
                result = self.run_cli("businesses", "list", cwd=tmp, env=self.synthetic_env(tmp, server))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("PRIVATE_PROVIDER_ERROR", result.stderr)
                self.assertEqual(len(server.requests), 1)
            state["response"] = 200, {"businesses": []}
            result = self.run_cli("businesses", "list", cwd=tmp, env=self.synthetic_env(tmp, server))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"businesses": []})

    def test_businesses_mutation_unconfirmed(self):
        business = {"id": "business-synthetic", "locationId": "location-synthetic", "name": "Synthetic Business"}
        bad_businesses = [None, {}, {**business, "id": "../invalid"}, {**business, "locationId": "location-other"}]
        bad_businesses += [{field: value for field, value in business.items() if field != key} for key in ("id", "locationId")]
        failures = [(code, {"private": "PRIVATE_PROVIDER_ERROR"}) for code in (403, 429)]
        failures += [(200, payload) for payload in (b"PRIVATE_PROVIDER_ERROR", [], {}, {"success": False}, {"success": "true"}, {"success": 1})]
        state = {"response": None}
        def respond(request):
            return (200, {"business": business}) if request["method"] == "GET" else state["response"]
        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            for command, flags in (("create", ["--name", business["name"]]), ("update", [business["id"], "--name", "Renamed"]), ("delete", [business["id"], "--confirm-delete", business["id"]])):
                responses = list(failures)
                if command != "delete":
                    responses += [(200, {"success": True, "business": value}) for value in bad_businesses]
                    responses += [(200, {"success": True, "buiseness": business})]
                if command == "update":
                    responses += [(200, {"success": True, "business": {**business, "id": "business-other"}})]
                for response in responses:
                    with self.subTest(command=command, response=response):
                        state["response"] = response
                        server.requests.clear()
                        result = self.run_cli("--yes", "businesses", command, *flags, cwd=tmp, env=self.synthetic_env(tmp, server))
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, "")
                        self.assertIn("unconfirmed", result.stderr)
                        self.assertIn("No retry or rollback", result.stderr)
                        self.assertNotIn("PRIVATE_PROVIDER_ERROR", result.stderr)
                        self.assertEqual(len([request for request in server.requests if request["method"] != "GET"]), 1)

    def test_businesses_help_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = clean_env(tmp)
            env.pop("GHL_TEST_BASE_URL", None)
            result = self.run_cli("businesses", "--help", cwd=tmp, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(result.stdout.split("{")[1].split("}")[0].split(",")), {"list", "get", "create", "update", "delete"})
            result = self.run_cli("help", "agent", cwd=tmp, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            for text in ("businesses list", "businesses get", "businesses create", "businesses update", "businesses delete", "with sources", "additional details", "Never invent", "name-only creation is noninteractive", "businesses.readonly", "businesses.write", "companyName text only"):
                self.assertIn(text, result.stdout)
            result = self.run_cli("businesses", "create", "--name", "Synthetic Business", cwd=tmp, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing GHL_LOCATION_ID", result.stderr)
            env["GHL_LOCATION_ID"] = "location-synthetic"
            result = self.run_cli("businesses", "create", "--name", "Synthetic Business", cwd=tmp, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["body"], {"name": "Synthetic Business", "locationId": "location-synthetic"})

    def test_businesses_list_get(self):
        business = {"id": "business-synthetic", "locationId": "location-synthetic", "name": "Synthetic Business"}
        rows = [business, {**business, "id": "business-second"}]
        def respond(request):
            return 200, {"businesses": rows} if request["path"] == "/businesses/" else {"business": business}
        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            env = self.synthetic_env(tmp, server)
            for flags, query in [((), {"limit": ["100"], "skip": ["0"]}), (("--limit", "150", "--skip", "3"), {"limit": ["150"], "skip": ["3"]})]:
                result = self.run_cli("businesses", "list", *flags, cwd=tmp, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"businesses": rows})
                self.assertEqual(server.requests[-1]["query"], {"locationId": ["location-synthetic"], **query})
            result = self.run_cli("businesses", "get", business["id"], cwd=tmp, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"business": business})
            self.assertEqual(server.requests[-1]["path"], "/businesses/business-synthetic")
            self.assertTrue(all(request["method"] == "GET" and request["version"] == "v3" for request in server.requests))

    def run_cli(self, *args, cwd=None, env=None, input_text=None):
        return subprocess.run(
            [str(CLI), *args],
            cwd=cwd or ROOT,
            env=env,
            text=True,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def run_cli_with_provider(self, responder, *args):
        def normalized_query(request):
            return {
                key: values[0] if len(values) == 1 else values
                for key, values in request["query"].items()
            }

        def respond(request):
            response = responder(request["path"], normalized_query(request))
            return response if isinstance(response, tuple) else (200, response)

        with SyntheticGhlServer(respond) as server:
            with tempfile.TemporaryDirectory() as tmp:
                env = self.synthetic_env(tmp, server)
                env["GHL_LOCATION_ID"] = "synthetic-location"
                result = self.run_cli(*args, cwd=tmp, env=env)
        requests = [
            (request["path"], normalized_query(request))
            for request in server.requests
        ]
        return result, requests

    def synthetic_env(self, tmp, server):
        env = clean_env(tmp)
        env["GHL_LOCATION_ID"] = "location-synthetic"
        env["GHL_PRIVATE_INTEGRATION_TOKEN"] = "token-synthetic"
        env["GHL_TEST_BASE_URL"] = server.url
        return env

    def run_logged_messages_fixture(self, tmp, comments, source_guids):
        def respond(request):
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": "Synthetic Person",
                            "phone": "2025550101",
                        }
                    ]
                }
            if request["path"] == "/conversations/search":
                return 200, {
                    "conversations": [
                        {
                            "id": "conversation-synthetic",
                            "contactId": "contact-synthetic",
                            "locationId": "location-synthetic",
                        }
                    ]
                }
            if request["path"] == "/conversations/conversation-synthetic/messages":
                return 200, {
                    "messages": {
                        "lastMessageId": "message-final",
                        "nextPage": False,
                        "messages": [
                            {
                                "id": f"comment-{index}",
                                "messageType": "TYPE_INTERNAL_COMMENT",
                                "contactId": "contact-synthetic",
                                "locationId": "location-synthetic",
                                "conversationId": "conversation-synthetic",
                                "body": body,
                            }
                            for index, body in enumerate(comments)
                        ],
                    }
                }
            return 404, {"message": "synthetic route not found"}

        with SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "2025550101",
                        "sourceGuids": source_guids,
                    }
                ),
            )
        return result, server.requests

    def log_capture_input(self, **overrides):
        value = {
            "contactName": "Synthetic Person",
            "phone": "2025550101",
            "start": "2026-08-03T13:00:00Z",
            "end": "2026-08-03T14:00:00Z",
            "summary": "Synthetic private summary",
            "transcript": [
                {
                    "sourceGuid": "synthetic-guid-a",
                    "timestamp": "2026-08-03T13:15:00Z",
                    "direction": "incoming",
                    "body": "Synthetic private body",
                },
                {
                    "sourceGuid": "synthetic-guid-b",
                    "timestamp": "2026-08-03T13:30:00Z",
                    "direction": "outgoing",
                    "body": "Synthetic private response",
                },
            ],
            "attachmentReferences": [
                {
                    "sourceGuid": "synthetic-guid-a",
                    "name": "synthetic-private.txt",
                    "mimeType": "text/plain",
                    "sizeBytes": 42,
                }
            ],
        }
        value.update(overrides)
        return value

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

    def test_logged_messages_resolves_exact_contact_and_conversation_without_overlap(self):
        def respond(request):
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": "Synthetic Person",
                            "phone": "+1 (202) 555-0101",
                        }
                    ]
                }
            if request["path"] == "/conversations/search":
                return 200, {
                    "conversations": [
                        {
                            "id": "conversation-synthetic",
                            "contactId": "contact-synthetic",
                            "locationId": "location-synthetic",
                        }
                    ]
                }
            if request["path"] == "/conversations/conversation-synthetic/messages":
                return 200, {
                    "messages": {
                        "lastMessageId": "",
                        "nextPage": False,
                        "messages": [],
                    }
                }
            return 404, {"message": "synthetic route not found"}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "202-555-0101",
                        "sourceGuids": ["synthetic-guid-a", "synthetic-guid-b"],
                    }
                ),
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"overlap": False, "classification": "none"},
        )
        self.assertEqual(server.requests[0]["method"], "POST")
        self.assertEqual(server.requests[0]["body"]["query"], "Synthetic Person")
        self.assertEqual(
            server.requests[2]["query"],
            {"limit": ["100"], "type": ["TYPE_INTERNAL_COMMENTS"]},
        )

    def test_logged_messages_reads_all_contact_search_pages_before_resolving(self):
        first_page = [
            {
                "id": f"other-contact-{index}",
                "name": "Other Synthetic Person",
                "phone": f"202555{1000 + index:04d}",
            }
            for index in range(99)
        ]
        first_page.append(
            {
                "id": "contact-first",
                "name": "Synthetic Person",
                "phone": "2025550101",
            }
        )

        def respond(request):
            if request["path"] == "/contacts/search":
                if request["body"]["page"] == 1:
                    return 200, {"contacts": first_page}
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-second",
                            "name": "Synthetic Person",
                            "phone": "+1 202 555 0101",
                        }
                    ]
                }
            return 500, {"private": "provider details must be hidden"}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "202-555-0101",
                        "sourceGuids": ["synthetic-guid-a"],
                    }
                ),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "error: contact resolution failed\n")
        self.assertEqual(
            [request["body"]["page"] for request in server.requests],
            [1, 2],
        )

    def test_logged_messages_rejects_contact_from_another_location(self):
        def respond(request):
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "other-location",
                            "name": "Synthetic Person",
                            "additionalPhones": [{"phone": "+1 202 555 0101"}],
                        }
                    ]
                }
            return 200, {
                "conversations": [
                    {
                        "id": "conversation-synthetic",
                        "contactId": "contact-synthetic",
                        "locationId": "location-synthetic",
                    }
                ]
            }

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "202-555-0101",
                        "sourceGuids": ["synthetic-guid-a"],
                    }
                ),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "error: contact resolution failed\n")
        self.assertEqual(len(server.requests), 1)

    def test_logged_messages_rejects_truncated_or_ambiguous_conversation_search(self):
        def respond(request):
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": "Synthetic Person",
                            "phone": "2025550101",
                        }
                    ]
                }
            if request["path"] == "/conversations/search":
                return 200, {
                    "total": 2,
                    "conversations": [
                        {
                            "id": "conversation-synthetic",
                            "contactId": "contact-synthetic",
                            "locationId": "location-synthetic",
                        }
                    ],
                }
            return 500, {"private": "provider details must be hidden"}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "2025550101",
                        "sourceGuids": ["synthetic-guid-a"],
                    }
                ),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "error: conversation resolution failed\n")
        self.assertEqual(len(server.requests), 2)

    def test_logged_messages_paginates_nested_internal_comment_envelope(self):
        def respond(request):
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": "Synthetic Person",
                            "phone": "2025550101",
                        }
                    ]
                }
            if request["path"] == "/conversations/search":
                return 200, {
                    "conversations": [
                        {
                            "id": "conversation-synthetic",
                            "contactId": "contact-synthetic",
                            "locationId": "location-synthetic",
                        }
                    ]
                }
            if request["path"] == "/conversations/conversation-synthetic/messages":
                if "lastMessageId" not in request["query"]:
                    return 200, {
                        "messages": {
                            "lastMessageId": "message-cursor",
                            "nextPage": True,
                            "messages": [
                                {
                                    "id": "message-non-comment",
                                    "messageType": "TYPE_SMS",
                                    "body": (
                                        "--- message-monitor:v1 ---\n"
                                        '{"captureId":"' + "9" * 64
                                        + '","sourceGuids":["synthetic-guid-a"]}\n'
                                        "--- end-message-monitor ---"
                                    ),
                                },
                                {
                                    "id": "message-comment-one",
                                    "messageType": "TYPE_INTERNAL_COMMENT",
                                    "contactId": "contact-synthetic",
                                    "locationId": "location-synthetic",
                                    "conversationId": "conversation-synthetic",
                                    "body": "ordinary synthetic comment",
                                },
                            ],
                        }
                    }
                return 200, {
                    "messages": {
                        "lastMessageId": "message-final",
                        "nextPage": False,
                        "messages": [
                            {
                                "id": "message-comment-two",
                                "messageType": "TYPE_INTERNAL_COMMENT",
                                "contactId": "contact-synthetic",
                                "locationId": "location-synthetic",
                                "conversationId": "conversation-synthetic",
                                "body": "another ordinary synthetic comment",
                            }
                        ],
                    }
                }
            return 404, {"message": "synthetic route not found"}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "2025550101",
                        "sourceGuids": ["synthetic-guid-a"],
                    }
                ),
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"overlap": False, "classification": "none"},
        )
        message_requests = [
            request for request in server.requests if request["path"].endswith("/messages")
        ]
        self.assertEqual(len(message_requests), 2)
        self.assertEqual(message_requests[1]["query"]["lastMessageId"], ["message-cursor"])
        self.assertEqual(
            message_requests[1]["query"]["type"],
            ["TYPE_INTERNAL_COMMENTS"],
        )

    def test_logged_messages_bounds_fresh_cursor_pagination(self):
        message_page = 0

        def respond(request):
            nonlocal message_page
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": "Synthetic Person",
                            "phone": "2025550101",
                        }
                    ]
                }
            if request["path"] == "/conversations/search":
                return 200, {
                    "conversations": [
                        {
                            "id": "conversation-synthetic",
                            "contactId": "contact-synthetic",
                            "locationId": "location-synthetic",
                        }
                    ]
                }
            if request["path"] == "/conversations/conversation-synthetic/messages":
                message_page += 1
                return 200, {
                    "messages": {
                        "lastMessageId": f"fresh-cursor-{message_page}",
                        "nextPage": message_page <= 1000,
                        "messages": [],
                    }
                }
            return 404, {"message": "synthetic route not found"}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "2025550101",
                        "sourceGuids": ["synthetic-guid-a"],
                    }
                ),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "error: internal comment history unavailable\n")
        self.assertEqual(message_page, 1000)

    def test_logged_messages_rejects_provider_pages_over_the_requested_limit(self):
        def respond(request):
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": "Synthetic Person",
                            "phone": "2025550101",
                        }
                    ]
                }
            if request["path"] == "/conversations/search":
                return 200, {
                    "conversations": [
                        {
                            "id": "conversation-synthetic",
                            "contactId": "contact-synthetic",
                            "locationId": "location-synthetic",
                        }
                    ]
                }
            if request["path"] == "/conversations/conversation-synthetic/messages":
                return 200, {
                    "messages": {
                        "lastMessageId": "cursor-synthetic",
                        "nextPage": False,
                        "messages": [{} for _index in range(101)],
                    }
                }
            return 404, {"message": "synthetic route not found"}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "2025550101",
                        "sourceGuids": ["synthetic-guid-a"],
                    }
                ),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "error: internal comment history unavailable\n")

    def test_logged_messages_fails_closed_on_contact_cardinality_and_phone_resolution(self):
        cases = {
            "zero": [],
            "unresolved-phone": [
                {
                    "id": "contact-synthetic",
                    "locationId": "location-synthetic",
                    "name": "Synthetic Person",
                    "phone": "2025550199",
                }
            ],
            "multiple": [
                {
                    "id": f"contact-synthetic-{index}",
                    "locationId": "location-synthetic",
                    "name": "Synthetic Person",
                    "phone": "2025550101",
                }
                for index in range(2)
            ],
            "invalid-id": [
                {
                    "id": "private-contact\nforged",
                    "locationId": "location-synthetic",
                    "name": "Synthetic Person",
                    "phone": "2025550101",
                }
            ],
            "invalid-name-type": [
                {
                    "id": "contact-synthetic",
                    "locationId": "location-synthetic",
                    "name": ["Synthetic Person"],
                    "phone": "2025550101",
                }
            ],
        }
        for name, contacts in cases.items():
            with self.subTest(name=name):
                def respond(request):
                    if request["path"] == "/contacts/search":
                        return 200, {"contacts": contacts}
                    return 500, {"private": "must not be returned"}

                with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
                    result = self.run_cli(
                        "conversations",
                        "logged-messages",
                        cwd=tmp,
                        env=self.synthetic_env(tmp, server),
                        input_text=json.dumps(
                            {
                                "contactName": "Synthetic Person",
                                "phone": "2025550101",
                                "sourceGuids": ["synthetic-guid-a"],
                            }
                        ),
                    )

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "error: contact resolution failed\n")
                self.assertNotIn("private-contact", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(len(server.requests), 1)

    def test_private_commands_fail_closed_on_non_list_additional_phones(self):
        command_inputs = {
            "logged-messages": {
                "contactName": "Synthetic Person",
                "phone": "2025550101",
                "sourceGuids": ["synthetic-guid-a"],
            },
            "log-capture": self.log_capture_input(),
        }
        for value_name, additional_phones in {
            "integer": 42,
            "boolean": True,
        }.items():
            for command, private_input in command_inputs.items():
                with self.subTest(value=value_name, command=command):
                    def respond(request):
                        if request["path"] == "/contacts/search":
                            return 200, {
                                "contacts": [
                                    {
                                        "id": "contact-synthetic",
                                        "locationId": "location-synthetic",
                                        "name": "Synthetic Person",
                                        "phone": "2025550101",
                                        "additionalPhones": additional_phones,
                                    }
                                ]
                            }
                        return 500, {"private": "must not be returned"}

                    with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(
                        respond
                    ) as server:
                        result = self.run_cli(
                            "conversations",
                            command,
                            cwd=tmp,
                            env=self.synthetic_env(tmp, server),
                            input_text=json.dumps(private_input),
                        )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(
                        result.stderr,
                        "error: contact resolution failed\n",
                    )
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual(len(server.requests), 1)

    def test_logged_messages_fails_closed_on_conversation_cardinality_or_association(self):
        cases = {
            "zero": [],
            "multiple": [
                {
                    "id": f"conversation-synthetic-{index}",
                    "contactId": "contact-synthetic",
                    "locationId": "location-synthetic",
                }
                for index in range(2)
            ],
            "wrong-association": [
                {
                    "id": "conversation-synthetic",
                    "contactId": "other-contact",
                    "locationId": "location-synthetic",
                }
            ],
            "invalid-id": [
                {
                    "id": "private-conversation\nforged",
                    "contactId": "contact-synthetic",
                    "locationId": "location-synthetic",
                }
            ],
        }
        for name, conversations in cases.items():
            with self.subTest(name=name):
                def respond(request):
                    if request["path"] == "/contacts/search":
                        return 200, {
                            "contacts": [
                                {
                                    "id": "contact-synthetic",
                                    "locationId": "location-synthetic",
                                    "name": "Synthetic Person",
                                    "phone": "2025550101",
                                }
                            ]
                        }
                    if request["path"] == "/conversations/search":
                        return 200, {"conversations": conversations}
                    return 500, {"private": "must not be returned"}

                with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
                    result = self.run_cli(
                        "conversations",
                        "logged-messages",
                        cwd=tmp,
                        env=self.synthetic_env(tmp, server),
                        input_text=json.dumps(
                            {
                                "contactName": "Synthetic Person",
                                "phone": "2025550101",
                                "sourceGuids": ["synthetic-guid-a"],
                            }
                        ),
                    )

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "error: conversation resolution failed\n")
                self.assertNotIn("private-conversation", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(len(server.requests), 2)

    def test_logged_messages_sanitizes_provider_errors_and_never_uses_write_routes(self):
        private_values = ["2025550101", "synthetic-guid-private", "private provider body"]

        def respond(request):
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": "Synthetic Person",
                            "phone": private_values[0],
                        }
                    ]
                }
            if request["path"] == "/conversations/search":
                return 200, {
                    "conversations": [
                        {
                            "id": "conversation-synthetic",
                            "contactId": "contact-synthetic",
                            "locationId": "location-synthetic",
                        }
                    ]
                }
            return 503, {"message": private_values[2], "guid": private_values[1]}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": private_values[0],
                        "sourceGuids": [private_values[1]],
                    }
                ),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "error: GHL request failed (HTTP 503)\n")
        for private_value in private_values:
            self.assertNotIn(private_value, result.stdout)
            self.assertNotIn(private_value, result.stderr)
        self.assertEqual(
            [(request["method"], request["path"]) for request in server.requests],
            [
                ("POST", "/contacts/search"),
                ("GET", "/conversations/search"),
                ("GET", "/conversations/conversation-synthetic/messages"),
            ],
        )

    def test_logged_messages_sanitizes_malformed_provider_responses(self):
        def respond(request):
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": "Synthetic Person",
                            "phone": "2025550101",
                        }
                    ]
                }
            return 200, b"private malformed provider response"

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "2025550101",
                        "sourceGuids": ["synthetic-guid-private"],
                    }
                ),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "error: GHL response was invalid\n")
        self.assertNotIn("private malformed provider response", result.stderr)
        self.assertNotIn("synthetic-guid-private", result.stderr)

    def test_logged_messages_sanitizes_provider_json_integer_limit_errors(self):
        oversized_integer = b'{"private":' + (b"9" * 5000) + b"}"

        def respond(_request):
            return 200, oversized_integer

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "2025550101",
                        "sourceGuids": ["synthetic-guid-private"],
                    }
                ),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "error: GHL response was invalid\n")
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(len(server.requests), 1)

    def test_logged_messages_refuses_cross_origin_provider_redirects(self):
        def redirected(_request):
            return 200, {"contacts": []}

        with SyntheticGhlServer(redirected) as redirect_target:
            def redirect(_request):
                return 302, {}, {"Location": f"{redirect_target.url}/private-target"}

            with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(redirect) as server:
                result = self.run_cli(
                    "conversations",
                    "logged-messages",
                    cwd=tmp,
                    env=self.synthetic_env(tmp, server),
                    input_text=json.dumps(
                        {
                            "contactName": "Synthetic Person",
                            "phone": "2025550101",
                            "sourceGuids": ["synthetic-guid-private"],
                        }
                    ),
                )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "error: GHL request failed (HTTP 302)\n")
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(redirect_target.requests, [])

    def test_test_endpoint_refuses_credentials_loaded_from_dotenv(self):
        def respond(_request):
            return 200, {"contacts": []}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "GHL_LOCATION_ID=private-dotenv-location\n"
                "GHL_PRIVATE_INTEGRATION_TOKEN=private-dotenv-token\n",
                encoding="utf-8",
            )
            env = clean_env(tmp)
            env["GHL_ENV_FILE"] = str(env_file)
            env["GHL_TEST_BASE_URL"] = server.url
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=env,
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "2025550101",
                        "sourceGuids": ["synthetic-guid-private"],
                    }
                ),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "error: test API endpoint requires explicit credentials\n",
        )
        self.assertNotIn("private-dotenv", result.stderr)
        self.assertEqual(server.requests, [])

    def test_test_endpoint_rejects_an_invalid_port_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = clean_env(tmp)
            env["GHL_LOCATION_ID"] = "location-synthetic"
            env["GHL_PRIVATE_INTEGRATION_TOKEN"] = "token-synthetic"
            env["GHL_TEST_BASE_URL"] = "http://localhost:not-a-port"
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=env,
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "2025550101",
                        "sourceGuids": ["synthetic-guid-private"],
                    }
                ),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "error: invalid test API endpoint\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_logged_messages_rejects_private_input_without_echoing_it(self):
        private_values = ["2025550101", "synthetic-guid-private"]
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=clean_env(tmp),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": private_values[0],
                        "sourceGuids": [private_values[1], private_values[1]],
                    }
                ),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "error: invalid private input\n")
        for private_value in private_values:
            self.assertNotIn(private_value, result.stdout)
            self.assertNotIn(private_value, result.stderr)

    def test_private_commands_reject_invalid_utf8_without_traceback(self):
        for command in ["logged-messages", "log-capture"]:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                result = subprocess.run(
                    [str(CLI), "conversations", command],
                    cwd=tmp,
                    env=clean_env(tmp),
                    input=b'{"private":"\xff"}',
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"error: invalid private input\n")
            self.assertNotIn(b"Traceback", result.stderr)

    def test_private_commands_reject_oversized_json_integer_without_traceback(self):
        payload = b'{"phone":' + (b"9" * 5000) + b"}"
        for command in ["logged-messages", "log-capture"]:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                result = subprocess.run(
                    [str(CLI), "conversations", command],
                    cwd=tmp,
                    env=clean_env(tmp),
                    input=payload,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"error: invalid private input\n")
            self.assertNotIn(b"Traceback", result.stderr)

    def test_logged_messages_rejects_surrogate_source_guids_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            proposed_result = self.run_cli(
                "conversations",
                "logged-messages",
                cwd=tmp,
                env=clean_env(tmp),
                input_text=json.dumps(
                    {
                        "contactName": "Synthetic Person",
                        "phone": "2025550101",
                        "sourceGuids": ["\ud800"],
                    }
                ),
            )

        self.assertNotEqual(proposed_result.returncode, 0)
        self.assertEqual(proposed_result.stdout, "")
        self.assertEqual(proposed_result.stderr, "error: invalid private input\n")
        self.assertNotIn("Traceback", proposed_result.stderr)

        metadata = json.dumps(
            {"captureId": "a" * 64, "sourceGuids": ["\ud800"]},
            separators=(",", ":"),
        )
        body = (
            "--- message-monitor:v1 ---\n"
            f"{metadata}\n"
            "--- end-message-monitor ---"
        )
        with tempfile.TemporaryDirectory() as tmp:
            stored_result, _requests = self.run_logged_messages_fixture(
                tmp,
                [body],
                ["synthetic-guid-a"],
            )

        self.assertEqual(stored_result.returncode, 0, stored_result.stderr)
        self.assertEqual(
            json.loads(stored_result.stdout),
            {"overlap": True, "classification": "indeterminate"},
        )
        self.assertNotIn("Traceback", stored_result.stderr)

    def test_logged_messages_reports_exact_overlap_without_echoing_identifiers(self):
        source_guids = ["synthetic-guid-a", "synthetic-guid-b"]
        metadata = json.dumps(
            {"captureId": "a" * 64, "sourceGuids": source_guids},
            separators=(",", ":"),
        )
        body = (
            "Synthetic private comment body\n\n"
            "--- message-monitor:v1 ---\n"
            f"{metadata}\n"
            "--- end-message-monitor ---"
        )

        with tempfile.TemporaryDirectory() as tmp:
            result, _requests = self.run_logged_messages_fixture(tmp, [body], source_guids)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"overlap": True, "classification": "exact"},
        )
        for private_value in [*source_guids, "Synthetic private comment body"]:
            self.assertNotIn(private_value, result.stdout)
            self.assertNotIn(private_value, result.stderr)

    def test_logged_messages_reports_partial_overlap(self):
        metadata = json.dumps(
            {
                "captureId": "b" * 64,
                "sourceGuids": ["synthetic-guid-b", "synthetic-guid-c"],
            },
            separators=(",", ":"),
        )
        body = (
            "--- message-monitor:v1 ---\n"
            f"{metadata}\n"
            "--- end-message-monitor ---"
        )

        with tempfile.TemporaryDirectory() as tmp:
            result, _requests = self.run_logged_messages_fixture(
                tmp,
                [body],
                ["synthetic-guid-a", "synthetic-guid-b"],
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"overlap": True, "classification": "partial"},
        )

    def test_logged_messages_treats_malformed_or_unsupported_metadata_as_indeterminate(self):
        malformed_bodies = [
            (
                "--- message-monitor:v1 ---\n"
                '{"captureId":"not-a-hash","sourceGuids":["synthetic-guid-a"]}\n'
                "--- end-message-monitor ---"
            ),
            (
                "--- message-monitor:v2 ---\n"
                '{"captureId":"' + "c" * 64 + '","sourceGuids":["synthetic-guid-a"]}\n'
                "--- end-message-monitor ---"
            ),
            (
                "--- message-monitor:v1 ---\n"
                '{"captureId":"' + "d" * 64 + '","sourceGuids":["synthetic-guid-a"]}\n'
                "--- end-message-monitor ---\ntrailing text"
            ),
            (
                "--- message-monitor:v1 ---\n"
                '{"captureId":"' + "e" * 64 + '","sourceGuids":["synthetic-guid-a"]}\n'
                "--- end-message-monitor ---\n"
                "--- message-monitor:v1 ---\n"
                '{"captureId":"' + "f" * 64 + '","sourceGuids":["synthetic-guid-b"]}\n'
                "--- end-message-monitor ---"
            ),
            (
                "--- message-monitor:v2 ---\nunsupported metadata\n"
                "--- message-monitor:v1 ---\n"
                '{"captureId":"' + "a" * 64 + '","sourceGuids":["synthetic-guid-z"]}\n'
                "--- end-message-monitor ---"
            ),
            (
                "--- message-monitor:v1 ---\n"
                '{"captureId":' + "9" * 5000 + ',"sourceGuids":["synthetic-guid-z"]}\n'
                "--- end-message-monitor ---"
            ),
        ]

        for body in malformed_bodies:
            with self.subTest(body=body[:24]), tempfile.TemporaryDirectory() as tmp:
                result, _requests = self.run_logged_messages_fixture(
                    tmp,
                    [body],
                    ["synthetic-guid-z"],
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout),
                {"overlap": True, "classification": "indeterminate"},
            )
            self.assertEqual(result.stderr, "")

    def test_logged_messages_malformed_metadata_overrides_an_exact_match(self):
        exact_metadata = json.dumps(
            {
                "captureId": "e" * 64,
                "sourceGuids": ["synthetic-guid-a"],
            },
            separators=(",", ":"),
        )
        exact_body = (
            "--- message-monitor:v1 ---\n"
            f"{exact_metadata}\n"
            "--- end-message-monitor ---"
        )
        malformed_body = (
            "--- message-monitor:v1 ---\n"
            '{"captureId":"invalid","sourceGuids":["synthetic-guid-z"]}\n'
            "--- end-message-monitor ---"
        )

        with tempfile.TemporaryDirectory() as tmp:
            result, _requests = self.run_logged_messages_fixture(
                tmp,
                [exact_body, malformed_body],
                ["synthetic-guid-a"],
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"overlap": True, "classification": "indeterminate"},
        )

    def test_log_capture_defaults_to_private_dry_run_after_exact_resolution(self):
        private_values = [
            "Synthetic Person",
            "2025550101",
            "Synthetic private summary",
            "Synthetic private body",
            "Synthetic private response",
            "synthetic-guid-a",
            "synthetic-guid-b",
            "synthetic-private.txt",
        ]

        def respond(request):
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": private_values[0],
                            "phone": private_values[1],
                        }
                    ]
                }
            if request["path"] == "/conversations/search":
                return 200, {
                    "conversations": [
                        {
                            "id": "conversation-synthetic",
                            "contactId": "contact-synthetic",
                            "locationId": "location-synthetic",
                        }
                    ]
                }
            return 500, {"private": "unexpected write"}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "conversations",
                "log-capture",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(self.log_capture_input()),
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "dryRun": True,
                "operation": "create one private Conversation Internal Comment",
                "message": "Re-run with --yes to execute.",
            },
        )
        self.assertEqual(result.stderr, "")
        for private_value in private_values:
            self.assertNotIn(private_value, result.stdout)
            self.assertNotIn(private_value, result.stderr)
        self.assertEqual(
            [(request["method"], request["path"]) for request in server.requests],
            [("POST", "/contacts/search"), ("GET", "/conversations/search")],
        )

    def test_log_capture_executes_one_fixed_internal_comment_and_verifies_readback(self):
        private_values = [
            "Synthetic Person",
            "2025550101",
            "contact-synthetic",
            "conversation-synthetic",
            "message-synthetic",
            "Synthetic private summary",
            "Synthetic private body",
            "Synthetic private response",
            "synthetic-guid-a",
            "synthetic-guid-b",
            "synthetic-private.txt",
        ]
        created_body = None

        def respond(request):
            nonlocal created_body
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": "Synthetic Person",
                            "phone": "2025550101",
                        }
                    ]
                }
            if request["path"] == "/conversations/search":
                return 200, {
                    "conversations": [
                        {
                            "id": "conversation-synthetic",
                            "contactId": "contact-synthetic",
                            "locationId": "location-synthetic",
                        }
                    ]
                }
            if request["method"] == "POST" and request["path"] == "/conversations/messages":
                created_body = request["body"]["message"]
                return 201, {
                    "conversationId": "conversation-synthetic",
                    "messageId": "message-synthetic",
                }
            if request["path"] == "/conversations/messages/message-synthetic":
                return 200, {
                    "message": {
                        "id": "message-synthetic",
                        "contactId": "contact-synthetic",
                        "conversationId": "conversation-synthetic",
                        "messageType": "TYPE_INTERNAL_COMMENT",
                        "status": "delivered",
                        "body": created_body,
                    }
                }
            return 500, {"private": "unexpected route"}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "--yes",
                "conversations",
                "log-capture",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(self.log_capture_input()),
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"created": True, "verified": True})
        self.assertEqual(result.stderr, "")
        for private_value in private_values:
            self.assertNotIn(private_value, result.stdout)
            self.assertNotIn(private_value, result.stderr)

        self.assertEqual(
            [(request["method"], request["path"]) for request in server.requests],
            [
                ("POST", "/contacts/search"),
                ("GET", "/conversations/search"),
                ("POST", "/conversations/messages"),
                ("GET", "/conversations/messages/message-synthetic"),
            ],
        )
        write = server.requests[2]
        self.assertEqual(write["version"], "v3")
        self.assertEqual(
            set(write["body"]),
            {"type", "contactId", "message", "status", "mentions"},
        )
        self.assertEqual(write["body"]["type"], "InternalComment")
        self.assertEqual(write["body"]["contactId"], "contact-synthetic")
        self.assertEqual(write["body"]["status"], "delivered")
        self.assertEqual(write["body"]["mentions"], [])
        comment = write["body"]["message"]
        self.assertIn("Logged from a personal phone conversation.", comment)
        self.assertIn("Capture Summary\nSynthetic private summary", comment)
        self.assertIn("Transcript", comment)
        self.assertIn("Synthetic private body", comment)
        self.assertIn("Attachment References", comment)
        self.assertIn("synthetic-private.txt (text/plain, 42 bytes)", comment)
        self.assertTrue(
            comment.endswith(
                "--- message-monitor:v1 ---\n"
                '{"captureId":"16bb3f43cc2b941b445b25de7a59b31b4f3d051bfa40fd5d4a499cacb3a71f76",'
                '"sourceGuids":["synthetic-guid-a","synthetic-guid-b"]}\n'
                "--- end-message-monitor ---"
            )
        )

    def test_log_capture_rejects_invalid_unicode_with_a_fixed_private_error(self):
        capture = self.log_capture_input(summary="private-surrogate-\ud800")

        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli(
                "--yes",
                "conversations",
                "log-capture",
                cwd=tmp,
                env=clean_env(tmp),
                input_text=json.dumps(capture),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "error: invalid private input\n")
        self.assertNotIn("private-surrogate", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_log_capture_rejects_unsupported_metadata_guids_associations_and_mentions(self):
        base = self.log_capture_input()
        cases = {}
        for field, value in [
            ("type", "SMS"),
            ("mentions", ["synthetic-user"]),
            ("metadata", {"unsupported": True}),
            ("truncate", True),
            ("split", True),
        ]:
            candidate = dict(base)
            candidate[field] = value
            cases[f"top-level-{field}"] = candidate

        duplicate_guid = dict(base)
        duplicate_guid["transcript"] = [
            dict(base["transcript"][0]),
            {**base["transcript"][1], "sourceGuid": "synthetic-guid-a"},
        ]
        cases["duplicate-guid"] = duplicate_guid

        unassociated_attachment = dict(base)
        unassociated_attachment["attachmentReferences"] = [
            {**base["attachmentReferences"][0], "sourceGuid": "synthetic-guid-unknown"}
        ]
        cases["unassociated-attachment"] = unassociated_attachment

        for name, source_guid in [
            ("array-attachment-source-guid", ["synthetic-guid-a"]),
            ("object-attachment-source-guid", {"value": "synthetic-guid-a"}),
        ]:
            invalid_attachment = dict(base)
            invalid_attachment["attachmentReferences"] = [
                {**base["attachmentReferences"][0], "sourceGuid": source_guid}
            ]
            cases[name] = invalid_attachment

        path_attachment = dict(base)
        path_attachment["attachmentReferences"] = [
            {**base["attachmentReferences"][0], "name": "/private/path.txt"}
        ]
        cases["attachment-path"] = path_attachment

        out_of_order = dict(base)
        out_of_order["transcript"] = [
            {**base["transcript"][0], "timestamp": "2026-08-03T13:45:00Z"},
            {**base["transcript"][1], "timestamp": "2026-08-03T13:30:00Z"},
        ]
        cases["out-of-order"] = out_of_order

        outside_window = dict(base)
        outside_window["transcript"] = [
            {**base["transcript"][0], "timestamp": base["end"]},
            dict(base["transcript"][1]),
        ]
        cases["outside-window"] = outside_window

        mention = dict(base)
        mention["summary"] = "Private @person<userId>synthetic-user</userId> mention"
        cases["mention"] = mention

        direction_control = dict(base)
        direction_control["transcript"] = [
            {**base["transcript"][0], "direction": "incoming\nforged"},
            dict(base["transcript"][1]),
        ]
        cases["direction-control"] = direction_control

        reserved_metadata = dict(base)
        reserved_metadata["transcript"] = [
            {
                **base["transcript"][0],
                "body": "--- message-monitor:v1 --- private injected block",
            },
            dict(base["transcript"][1]),
        ]
        cases["reserved-metadata"] = reserved_metadata

        for name, capture in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                result = self.run_cli(
                    "--yes",
                    "conversations",
                    "log-capture",
                    cwd=tmp,
                    env=clean_env(tmp),
                    input_text=json.dumps(capture),
                )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "error: invalid private input\n")

    def test_log_capture_refuses_an_oversized_comment_before_write(self):
        capture = self.log_capture_input(summary="x" * (8 * 1024 * 1024))

        def respond(request):
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": "Synthetic Person",
                            "phone": "2025550101",
                        }
                    ]
                }
            if request["path"] == "/conversations/search":
                return 200, {
                    "conversations": [
                        {
                            "id": "conversation-synthetic",
                            "contactId": "contact-synthetic",
                            "locationId": "location-synthetic",
                        }
                    ]
                }
            return 500, {"private": "write must not occur"}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "--yes",
                "conversations",
                "log-capture",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(capture),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "error: formatted comment exceeds 8 MiB ceiling\n",
        )
        self.assertEqual(
            [(request["method"], request["path"]) for request in server.requests],
            [("POST", "/contacts/search"), ("GET", "/conversations/search")],
        )

    def test_log_capture_sanitizes_provider_rejection_without_retrying(self):
        private_values = [
            "Synthetic private summary",
            "Synthetic private body",
            "synthetic-guid-a",
            "private provider rejection",
        ]

        def respond(request):
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": "Synthetic Person",
                            "phone": "2025550101",
                        }
                    ]
                }
            if request["path"] == "/conversations/search":
                return 200, {
                    "conversations": [
                        {
                            "id": "conversation-synthetic",
                            "contactId": "contact-synthetic",
                            "locationId": "location-synthetic",
                        }
                    ]
                }
            return 413, {"message": private_values[-1], "raw": private_values[2]}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "--yes",
                "conversations",
                "log-capture",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(self.log_capture_input()),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "error: GHL request failed (HTTP 413)\n")
        self.assertNotIn("limit", result.stderr.lower())
        for private_value in private_values:
            self.assertNotIn(private_value, result.stderr)
        self.assertEqual(len(server.requests), 3)

    def test_log_capture_sanitizes_readback_failure_without_claiming_success(self):
        private_error = "private readback provider error"

        def respond(request):
            if request["path"] == "/contacts/search":
                return 200, {
                    "contacts": [
                        {
                            "id": "contact-synthetic",
                            "locationId": "location-synthetic",
                            "name": "Synthetic Person",
                            "phone": "2025550101",
                        }
                    ]
                }
            if request["path"] == "/conversations/search":
                return 200, {
                    "conversations": [
                        {
                            "id": "conversation-synthetic",
                            "contactId": "contact-synthetic",
                            "locationId": "location-synthetic",
                        }
                    ]
                }
            if request["method"] == "POST":
                return 201, {
                    "conversationId": "conversation-synthetic",
                    "messageId": "message-synthetic",
                }
            return 503, {"message": private_error}

        with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
            result = self.run_cli(
                "--yes",
                "conversations",
                "log-capture",
                cwd=tmp,
                env=self.synthetic_env(tmp, server),
                input_text=json.dumps(self.log_capture_input()),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "error: GHL request failed (HTTP 503)\n")
        self.assertNotIn(private_error, result.stderr)
        self.assertNotIn("created", result.stdout)
        self.assertEqual(len(server.requests), 4)

    def test_log_capture_fails_closed_on_write_association_or_readback_mismatch(self):
        readback_base = {
            "id": "message-synthetic",
            "contactId": "contact-synthetic",
            "conversationId": "conversation-synthetic",
            "messageType": "TYPE_INTERNAL_COMMENT",
            "status": "delivered",
        }
        cases = {
            "write-conversation": ("write", {"conversationId": "other-conversation"}),
            "write-message-id": ("write", {"messageId": ""}),
            "write-message-id-control": (
                "write",
                {"messageId": "private-message-id\nforged"},
            ),
            "readback-id": ("readback", {"id": "other-message"}),
            "readback-contact": ("readback", {"contactId": "other-contact"}),
            "readback-conversation": (
                "readback",
                {"conversationId": "other-conversation"},
            ),
            "readback-type": ("readback", {"messageType": "TYPE_SMS"}),
            "readback-status": ("readback", {"status": "pending"}),
            "readback-body": ("readback", {"body": "private mismatched body"}),
            "readback-body-surrogate": ("readback", {"body": "\ud800"}),
        }

        for name, (stage, changes) in cases.items():
            with self.subTest(name=name):
                created_body = None

                def respond(request):
                    nonlocal created_body
                    if request["path"] == "/contacts/search":
                        return 200, {
                            "contacts": [
                                {
                                    "id": "contact-synthetic",
                                    "locationId": "location-synthetic",
                                    "name": "Synthetic Person",
                                    "phone": "2025550101",
                                }
                            ]
                        }
                    if request["path"] == "/conversations/search":
                        return 200, {
                            "conversations": [
                                {
                                    "id": "conversation-synthetic",
                                    "contactId": "contact-synthetic",
                                    "locationId": "location-synthetic",
                                }
                            ]
                        }
                    if request["method"] == "POST":
                        created_body = request["body"]["message"]
                        response = {
                            "conversationId": "conversation-synthetic",
                            "messageId": "message-synthetic",
                        }
                        if stage == "write":
                            response.update(changes)
                        return 201, response
                    message = {**readback_base, "body": created_body}
                    if stage == "readback":
                        message.update(changes)
                    return 200, {"message": message}

                with tempfile.TemporaryDirectory() as tmp, SyntheticGhlServer(respond) as server:
                    result = self.run_cli(
                        "--yes",
                        "conversations",
                        "log-capture",
                        cwd=tmp,
                        env=self.synthetic_env(tmp, server),
                        input_text=json.dumps(self.log_capture_input()),
                    )

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                expected = (
                    "error: internal comment write failed\n"
                    if stage == "write"
                    else "error: internal comment verification failed\n"
                )
                self.assertEqual(result.stderr, expected)
                self.assertNotIn("private mismatched body", result.stderr)
                self.assertNotIn("private-message-id", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(len(server.requests), 3 if stage == "write" else 4)

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

    def test_opportunity_search_all_accepts_provider_terminal_sentinels(self):
        page = {
            "opportunities": [{"id": f"opp-{index}"} for index in range(12)],
            "meta": {
                "total": 12,
                "currentPage": 1,
                "nextPage": "",
                "nextPageUrl": "https://services.leadconnectorhq.com/opportunities/search?startAfter=1&startAfterId=opp-11",
                "startAfter": 1,
                "startAfterId": "opp-11",
            },
        }

        result, requests = self.run_cli_with_provider(
            lambda _path, _query: page,
            "opportunities",
            "search",
            "--status",
            "open",
            "--all",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), page)
        self.assertEqual(len(requests), 1)

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

    def test_opportunity_search_all_accepts_string_start_after_cursor(self):
        pages = [
            {
                "opportunities": [
                    {"id": f"opp-{index}"} for index in range(100)
                ],
                "meta": {
                    "total": 101,
                    "startAfter": "1720000000000",
                    "startAfterId": "opp-99",
                },
            },
            {
                "opportunities": [{"id": "opp-100"}],
                "meta": {"total": 101},
            },
        ]

        result, requests = self.run_cli_with_provider(
            lambda _path, _query: pages.pop(0),
            "opportunities",
            "search",
            "--all",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["opportunities"]), 101)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1][1]["startAfter"], "1720000000000")
        self.assertEqual(requests[1][1]["startAfterId"], "opp-99")

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

    def test_opportunity_search_all_rejects_duplicate_ids_across_pages(self):
        pages = [
            {
                "opportunities": [{"id": "opp-1"}, {"id": "opp-2"}],
                "meta": {
                    "total": 3,
                    "nextPage": 2,
                    "startAfter": 1720000000000,
                    "startAfterId": "opp-2",
                },
            },
            {
                "opportunities": [{"id": "opp-2"}],
                "meta": {"total": 3},
            },
        ]

        result, requests = self.run_cli_with_provider(
            lambda _path, _query: pages.pop(0),
            "opportunities",
            "search",
            "--all",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repeated opportunity id 'opp-2'", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(requests), 2)

    def test_opportunity_search_all_rejects_duplicate_ids_within_page(self):
        page = {
            "opportunities": [{"id": "opp-1"}, {"id": "opp-1"}],
            "meta": {"total": 2},
        }

        result, requests = self.run_cli_with_provider(
            lambda _path, _query: page,
            "opportunities",
            "search",
            "--all",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repeated opportunity id 'opp-1'", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(requests), 1)

    def test_opportunity_search_all_rejects_continuing_empty_page(self):
        pages = [
            {
                "opportunities": [{"id": "opp-1"}],
                "meta": {
                    "total": 2,
                    "nextPage": 2,
                    "startAfter": 1720000000000,
                    "startAfterId": "opp-1",
                },
            },
            {
                "opportunities": [],
                "meta": {
                    "total": 2,
                    "nextPage": 3,
                    "startAfter": 1720000000001,
                    "startAfterId": "opp-empty-page",
                },
            },
        ]

        def responder(_path, _query):
            if not pages:
                return 500, {"message": "unexpected third request"}
            return pages.pop(0)

        result, requests = self.run_cli_with_provider(
            responder,
            "opportunities",
            "search",
            "--all",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("made no record progress", result.stderr)
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

    def test_opportunity_search_all_rejects_cross_type_repeated_cursor(self):
        page_number = 0

        def responder(_path, _query):
            nonlocal page_number
            page_number += 1
            start_after = 1720000000000 if page_number % 2 else "1720000000000"
            return {
                "opportunities": [
                    {"id": f"opp-{page_number}-{index}"} for index in range(100)
                ],
                "meta": {
                    "startAfter": start_after,
                    "startAfterId": "opp-boundary",
                },
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

    def test_help_agent_documents_the_only_guarded_conversation_write_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("help", "agent", cwd=tmp, env=clean_env(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("conversations search", result.stdout)
        self.assertIn("conversations messages", result.stdout)
        self.assertIn("ghl --yes conversations log-capture", result.stdout)
        self.assertIn("private JSON via stdin", result.stdout)
        self.assertIn("only Conversation write exception", result.stdout)

    def test_help_agent_documents_private_stdin_for_logged_message_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("help", "agent", cwd=tmp, env=clean_env(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("conversations logged-messages", result.stdout)
        self.assertIn("private JSON via stdin", result.stdout)

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
