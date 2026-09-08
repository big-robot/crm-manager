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
- Prefer least privilege. Do not enable payments, unrelated email-send, social,
  calendars, invoices, websites, courses, phone numbers, workflows, or AI-agent
  permissions for this CLI. The one Conversations write scope is limited by the
  CLI to the guarded private Internal Comment operation documented below.

Recommended steady-state permissions:

```json
[
  "contacts.readonly",
  "contacts.write",
  "businesses.readonly",
  "businesses.write",
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
  "conversations/message.readonly",
  "conversations/message.write"
]
```

Notes:

- Enable both read and write for contacts/opportunities/tags because the CLI
  searches before it writes.
- `pipelines.readonly` is needed to resolve pipeline and stage ids by name.
- `businesses.readonly` covers Business list/get and update/delete preflight;
  `businesses.write` covers Business mutations in the configured sub-account.
- The Conversations message write scope is used only by the fixed guarded
  `conversations log-capture` Internal Comment operation. The CLI exposes no
  generic send/reply/update/delete or customer-facing message write.
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
ghl businesses list --limit 100 --skip 0
ghl businesses get BUSINESS_ID
ghl tasks search --limit 100
ghl users list
ghl opportunities search --pipeline "Sales Pipeline" --status all --limit 20
ghl opportunities search --pipeline "Sales Pipeline" --status open --all
ghl tasks search --limit 100
ghl conversations search --contact-id CONTACT_ID
ghl conversations messages CONVERSATION_ID
ghl conversations logged-messages # reads contactName, phone, and sourceGuids as private JSON on stdin
ghl cleanup audit
```

Opportunity search keeps `--limit` as a single bounded provider request. Use
`--all` to follow the provider cursor through every matching page. Complete
search prints only after exhaustion is established; invalid pagination state or
a later-page failure exits without printing a partial opportunity list.

### Businesses

Businesses are organization records shown as Companies in a GHL sub-account,
distinct from Agency Companies. CRUD does not require a Contact:

```sh
ghl businesses create --name "Example Co"
ghl --yes businesses create --name "Example Co" --website "https://example.invalid"
ghl --yes businesses update BUSINESS_ID --description "Example description"
ghl --yes businesses delete BUSINESS_ID --confirm-delete BUSINESS_ID
```

Create requires `--name`; update accepts `--name`. Both accept optional `--phone`,
`--email`, `--website`, `--address`, `--city`, `--postal-code`, `--state`,
`--country`, and `--description`. Only supplied nonempty fields are sent, so a
name-only update preserves existing metadata. Field clearing is not offered.
Writes default to dry-run; scoped update/delete previews require read access.
List returns one bounded provider page with default `--limit 100 --skip 0` and
does not paginate automatically. `--limit` must be positive; `--skip` must be
nonnegative.

Before creating a Business, agents should offer known website, phone, email,
address, and description values with their sources, and ask whether the user has
additional details. Never invent missing values. This guidance does not add CLI
prompts: name-only creation is valid using the configured location.

Contact `--company` sets `companyName` text only; it does not establish a Business
association. Business renames do not update Contacts. Business deletion issues
no Contact writes; provider cascade behavior is unverified. If a write response
cannot confirm success and identity, the CLI exits nonzero and reports the write
as unconfirmed. It may already have occurred; no automatic retry or rollback is
attempted.

### Contact Business associations

```sh
ghl contacts business assign BUSINESS_ID --contact-id CONTACT_ID
ghl --yes contacts business assign BUSINESS_ID --contact-id CONTACT_ID --contact-id SECOND_CONTACT_ID
ghl --yes contacts business assign BUSINESS_ID --contact-id CONTACT_ID --replace
ghl --yes contacts business remove BUSINESS_ID --contact-id CONTACT_ID
```

Assignment sets the real Business relationship and synchronizes `companyName`
to that Business's current name. Replacing another Business requires `--replace`.
Removal requires the expected Business ID and rejects a different association.
It clears `companyName` with JSON null only when the text exactly matches the
Business's current name, including case. Differing text is preserved. A proven
unassociated Contact is a removal no-op, including its text.

Each command accepts 1 to 50 unique Contact IDs and validates the Business and
every Contact's identity and configured location before writing. There is no
batch splitting. Dry runs show existing and target associations, proposed name
updates, and planned requests; reads still run. Preflight is a snapshot and does
not prevent concurrent changes.

One bulk association write precedes the minimal name updates. Only IDs
affirmatively returned by the bulk operation can receive a name update; a valid
subset leaves other IDs unconfirmed and exits nonzero. Each name PUT is followed
by a scoped Contact GET to confirm the saved value. Clearing is confirmed only
when the GET omits `companyName`. A PUT response alone is not proof of a saved
name. Preserved or unchanged text is reported as such, not as a verified write.

Results include one outcome per requested Contact. `confirmed` identifies a
verified step, `unconfirmed` means a write may have occurred, and `unattempted`
means that step was not sent. `noop`, `preserved`, and `unchanged` describe the
preflight decisions. Failed preflight sends no writes. A request failure,
malformed bulk result, or failed name readback stops later writes and exits
nonzero with prior progress. No retry, rollback, or transaction guarantee is
provided. Inspect provider state before deciding how to continue.

Business reads require `businesses.readonly`; Business CRUD mutations require
`businesses.write`. Contact GET and PUT require `contacts.readonly` and
`contacts.write`, respectively. Bulk assignment/removal worked with the current
integration, but its isolated minimum scope was not established. Authorization
failures stop the command; they do not broaden permissions.

Business renames leave Contact text stale until explicitly repaired, for example
by assigning the already-associated Contacts again. Contact `--company` remains
text-only and neither creates a Business nor establishes an association.

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
ghl --yes conversations log-capture # approved private capture JSON via stdin
```

Deletes require an extra confirmation matching the record id:

```sh
ghl --yes contacts delete CONTACT_ID --confirm-delete CONTACT_ID
ghl --yes opportunities delete OPPORTUNITY_ID --confirm-delete OPPORTUNITY_ID
ghl --yes tasks delete TASK_ID --contact-id CONTACT_ID --confirm-delete TASK_ID
```

Task search shows every returned record and reports unlinked records. Add
`--exclude-unlinked` for a filtered operational view.

Do not add customer-facing or generic Conversation messaging, payment, or
social-posting commands to this CLI. The fixed guarded `log-capture` private
Internal Comment is the only Conversation write exception.

`conversations log-capture` accepts exactly `contactName`, `phone`, `start`,
`end`, `summary`, `transcript`, and `attachmentReferences` as private JSON on
stdin. It derives the complete fixed Internal Comment and final capture metadata,
defaults to a body-free dry run, and verifies an executed write by returned ID.
Never place its private input in command arguments, durable files, URLs, or logs.

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
