# GHL CLI Agent Instructions

Security first.

- Never print `GHL_PRIVATE_INTEGRATION_TOKEN`, full env, request auth headers, or raw secrets.
- Use the local CLI for GoHighLevel CRM work: `ghl` when installed on `PATH`, or `./bin/ghl` from the repo root.
- Prefer `ghl help agent` before using the CLI.
- Run `ghl doctor` when auth, permissions, config, or API state is unclear.
- The CLI automatically reads a repo-root `.env` file; never print its contents.
- Reads do not need `--yes`.
- Writes are dry-run unless `--yes` appears before the command, e.g. `ghl --yes opportunities update ...`.
- Deletes require both `--yes` and `--confirm-delete ID`.
- Do not add or run outbound email, SMS, social posting, payment, or GHL automation actions from this CLI without explicit user approval.

## Setup

For agent-led setup, use the bundled skill at `.agents/skills/ghl-cli-init/SKILL.md`.

Initialize a user-specific config:

```sh
ghl init
```

This creates `.ghl-cli.json`. If `.env` is missing, tell the user to copy `.env.example` to `.env` and set real values outside chat.

If the CLI is not installed on `PATH`, run from the repo root:

```sh
./bin/ghl init
```

## Common Commands

```sh
ghl help agent
ghl doctor
ghl pipelines
ghl fields --model opportunity
ghl tags list
ghl contacts search --query "Example Co" --limit 20
ghl tasks search --limit 100
ghl tasks search --limit 100 --include-unlinked
ghl users list
ghl opportunities search --pipeline "Sales Pipeline" --status all --limit 100
ghl conversations search --contact-id CONTACT_ID
```

## Contacts

Search first to avoid duplicates:

```sh
ghl contacts search --query "Jane" --limit 20
```

Upsert:

```sh
ghl --yes contacts upsert --name "Jane Doe" --email "jane@example.com" --company "Example Co" --tag prospect --field "Account Status=Prospect" --field "Primary Relationship=Decision Maker"
```

## Opportunities

Search first:

```sh
ghl opportunities search --pipeline "Sales Pipeline" --status all --limit 100
```

Create monthly recurring:

```sh
ghl --yes opportunities create --contact-id CONTACT_ID --name "Example Co - Managed Service" --pipeline "Sales Pipeline" --stage "Proposal Sent" --status open --value 18000 --field "Revenue Model=Monthly Recurring" --field "Monthly Amount=1500" --field "Forecast Term Months=12" --field "Forecast Value Basis=First-year recurring value"
```

Update:

```sh
ghl --yes opportunities update OPPORTUNITY_ID --pipeline "Sales Pipeline" --stage "Qualified" --value 0 --field "Next Step=Book discovery."
```

Next-step hygiene (constrained command — can only touch Next Step / Next Step Date;
the sanctioned command for unattended evidence-backed updates, see the knowledge-repo
CRM access policy):

```sh
ghl --yes opportunities next-step OPPORTUNITY_ID --step "Follow up after install." --date 2026-07-10
```

Delete:

```sh
ghl --yes opportunities delete OPPORTUNITY_ID --confirm-delete OPPORTUNITY_ID
```

## Tasks

Use GHL Tasks as the dated follow-up backlog for contacts, including contacts
that do not yet have opportunities. Writes remain dry-run unless `--yes` is
provided. The CLI does not expose task completion.

```sh
ghl tasks search --limit 100
ghl users list
ghl tasks create --contact-id CONTACT_ID --title "Follow up" --body "Context" --due 2026-07-14T09:00:00-04:00 --assigned-to USER_ID
ghl --yes tasks create --contact-id CONTACT_ID --title "Follow up" --body "Context" --due 2026-07-14T09:00:00-04:00 --assigned-to USER_ID
ghl --yes tasks update TASK_ID --contact-id CONTACT_ID --due 2026-07-20T09:00:00-04:00
ghl --yes tasks delete TASK_ID --contact-id CONTACT_ID --confirm-delete TASK_ID
```

Task search excludes records without a `contactId` and reports their count. That
is an actionable-backlog rule, not proof of deletion. Use `--include-unlinked`
for diagnostics.

## Conversations (read-only)

The email connector captures inbound and outbound customer email into GHL
conversations. This CLI exposes them read-only; no send, reply, update, or delete
subcommand exists, by policy. Requires `conversations.readonly` and
`conversations/message.readonly` token scopes.

```sh
ghl conversations search --contact-id CONTACT_ID
ghl conversations search --query "Example Co" --limit 20
ghl conversations messages CONVERSATION_ID --limit 50
ghl conversations email EMAIL_MESSAGE_ID
```

## Tests

```sh
python3 -m unittest discover -s tests
```

## Valuation Rule

- Monthly recurring opportunity value = expected first-year revenue.
- Store actual monthly price in `Monthly Amount`.
- Set `Forecast Term Months=12`.
- Set `Forecast Value Basis=First-year recurring value`.
- One-time opportunities use exact proposal total.
- Unpriced discovery opportunities stay value `0`, `Revenue Model=TBD`, `Forecast Value Basis=Discovery placeholder`.
