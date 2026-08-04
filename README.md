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
ghl contacts get CONTACT_ID
ghl tasks search --limit 100
ghl users list
ghl opportunities search --pipeline "Sales Pipeline" --status all
ghl tasks search --limit 100
ghl conversations search --contact-id CONTACT_ID
ghl conversations messages CONVERSATION_ID
ghl conversations logged-messages # reads contactName, phone, and sourceGuids as private JSON on stdin
ghl cleanup audit
```

`conversations logged-messages` is read-only. It resolves exactly one Contact
and associated existing Conversation, reads all Internal Comments, and reports
`none`, `exact`, `partial`, or fail-closed `indeterminate` overlap. Pass its
private JSON through stdin; never place phone numbers or source GUIDs in command
arguments, URLs, or logs.

Write commands are dry-run by default. Add `--yes` before the command to execute:

```sh
ghl --yes contacts upsert --name "Example Person" --email "person@example.com" --tag prospect
ghl --yes contacts create --name "Example Person" --email "person@example.invalid"
ghl --yes contacts update CONTACT_ID --phone-entry "Mobile=+15550101001" --phone-entry "Work=+15550101002" --additional-email "alternate@example.invalid"
ghl --yes contacts dnd CONTACT_ID --channel Email --status active
ghl --yes tasks create --contact-id CONTACT_ID --title "Follow up" --body "Context" --due 2026-07-14T09:00:00-04:00 --assigned-to USER_ID
ghl --yes tasks update TASK_ID --contact-id CONTACT_ID --due 2026-07-20T09:00:00-04:00
ghl --yes tasks complete TASK_ID --contact-id CONTACT_ID
```

Deletes require an extra confirmation matching the record id:

```sh
ghl --yes contacts delete CONTACT_ID --confirm-delete CONTACT_ID
ghl --yes opportunities delete OPPORTUNITY_ID --confirm-delete OPPORTUNITY_ID
ghl --yes tasks delete TASK_ID --contact-id CONTACT_ID --confirm-delete TASK_ID
```

Task search shows every returned record and reports unlinked records. Add
`--exclude-unlinked` for a filtered operational view.

Do not add outbound messaging, payment, or social-posting commands to this CLI.

`contacts create` is the explicit creation path. Unlike `contacts upsert`, it
does not intentionally select an existing contact through the location's
duplicate rules. Contact creation can still activate automations configured in
the GHL location, so preview and approve the exact record first.

For `contacts update`, repeated `--phone-entry LABEL=PHONE` values replace the
complete ordered GHL phone set. The first value becomes primary. Supported
labels are `Home`, `Work`, `Mobile`, `Landline`, and `Unlabeled`. Additional
emails are retained without labels because GHL does not support them.

For DND, `active` means DND is active for that channel. The constrained command
reads the current contact and preserves every untouched channel setting.

## Local Tests

Run the local smoke tests:

```sh
python3 -m unittest discover -s tests
```
