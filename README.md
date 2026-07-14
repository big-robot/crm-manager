# CRM Manager

Reusable GoHighLevel CRM CLI for agents serving their rainmaker operators.

## CLI

The CLI uses direct GHL REST API calls. It does not use the GHL MCP server.

Agents should start with:

```sh
ghl help agent
ghl doctor
```

See `AGENTS.md` for agent-specific safety rules and recipes.

If the CLI is not installed on `PATH`, run commands from the repo root with
`./bin/ghl`.

## Installation

Clone the repo and optionally put `ghl` on your `PATH`:

```sh
git clone https://github.com/big-robot/crm-manager.git
cd crm-manager
./bin/ghl --version
./bin/ghl --help
```

Optional symlink:

```sh
ln -s "$PWD/bin/ghl" /usr/local/bin/ghl
ghl --version
```

## Initialization

Create local config and env placeholders:

```sh
ghl init
```

This writes:

- `.ghl-cli.json`: user-specific defaults such as pipeline name, stage names,
  tags, and generic examples for `ghl help agent`.

If `.env` is missing, copy `.env.example` to `.env` and set real values outside
chat. The repository includes a generic `.env.example`.
See `.ghl-cli.example.json` for the config shape.

### Agent-led initialization

Agents can use the bundled skill at `.agents/skills/ghl-cli-init/SKILL.md` to run setup.
That workflow asks for non-secret defaults, runs `ghl init`, verifies generated
config, and leaves token setup to the user.

`.claude` is a symlink to `.agents` so Claude-style agents can find the same
repo-local instructions and skills without maintaining a duplicate tree.

Use noninteractive flags for scripted setup:

```sh
ghl init \
  --default-pipeline "Sales Pipeline" \
  --stages "New Lead,Engaged,Qualified,Meeting Booked,Discovery Complete,Proposal Sent,Nurture" \
  --tags "client,prospect,partner,do-not-contact,needs-cleanup" \
  --example-company "Example Co" \
  --example-contact "Jane Doe" \
  --example-email "jane@example.com"
```

Required environment:

Copy `.env.example` to `.env` and set these values, or export them in your shell:

```sh
export GHL_LOCATION_ID=...
export GHL_PRIVATE_INTEGRATION_TOKEN=...
```

The CLI automatically reads `.env` from the repo root. `.env` is gitignored.

## GoHighLevel API Token Setup

Create a private integration token in GHL:

```text
https://app.gohighlevel.com/v2/location/<locationId>/settings/private-integrations
```

Replace `<locationId>` with the target GHL location id. Save the location id as
`GHL_LOCATION_ID` and the private integration token as
`GHL_PRIVATE_INTEGRATION_TOKEN`.

Security:

- Do not commit the token.
- Do not print the token in chat, logs, shell history, or docs.
- Prefer least privilege. Do not enable payments, email-send, social, calendars,
  invoices, websites, courses, phone numbers, workflows, or AI-agent permissions
  for this CLI. Conversations permissions are read-only only.

Recommended steady-state permissions:

```json
[
  "contacts.readonly",
  "contacts.write",
  "opportunities.readonly",
  "opportunities.write",
  "pipelines.readonly",
  "locations/customFields.readonly",
  "locations/customFields.write",
  "locations/tags.readonly",
  "locations/tags.write",
  "locations/tasks.readonly",
  "users.readonly",
  "conversations.readonly",
  "conversations/message.readonly"
]
```

Notes:

- Enable both read and write for contacts/opportunities/tags because the CLI
  searches before it writes.
- `pipelines.readonly` is needed to resolve pipeline and stage ids by name.
- The conversations scopes are read-only on purpose: `ghl conversations` reads
  connector-captured email threads and has no send/reply/update/delete. Do not
  add `conversations/message.write`.
- `locations/customFields.readonly` is needed to map field names to ids.
- `locations/customFields.write` is needed for setup or repair of CRM fields.
- `locations/tasks.readonly` is needed to read the dated contact follow-up backlog.
- `contacts.write` also covers creating contact follow-up tasks.
- `users.readonly` is needed to find the assignee id for a task.
- Temporarily add `pipelines.write` and `pipelines.create` only when creating or
  repairing the pipeline through API tooling. Remove them afterward.
- After changing permissions or rotating the token, run:

```sh
ghl doctor
```

Read commands:

```sh
ghl help agent
ghl doctor
ghl pipelines
ghl fields --model opportunity
ghl tags list
ghl contacts search --query "Example Co"
ghl tasks search --limit 100
ghl users list
ghl opportunities search --pipeline "Sales Pipeline" --status all
ghl tasks search --limit 100
ghl conversations search --contact-id CONTACT_ID
ghl conversations messages CONVERSATION_ID
ghl cleanup audit
```

Write commands are dry-run by default. Add `--yes` before the command to execute:

```sh
ghl --yes contacts upsert --name "Example Person" --email "person@example.com" --tag prospect
ghl --yes tasks create --contact-id CONTACT_ID --title "Follow up" --body "Context" --due 2026-07-14T09:00:00-04:00 --assigned-to USER_ID
ghl --yes tasks update TASK_ID --contact-id CONTACT_ID --due 2026-07-20T09:00:00-04:00
```

Deletes require an extra confirmation matching the record id:

```sh
ghl --yes contacts delete CONTACT_ID --confirm-delete CONTACT_ID
ghl --yes opportunities delete OPPORTUNITY_ID --confirm-delete OPPORTUNITY_ID
ghl --yes tasks delete TASK_ID --contact-id CONTACT_ID --confirm-delete TASK_ID
```

Task search excludes records without contact linkage by default and reports
their count. Add `--include-unlinked` to audit them.

Do not add outbound messaging, payment, or social-posting commands to this CLI.

## Local Tests

Run the local smoke tests:

```sh
python3 -m unittest discover -s tests
```
