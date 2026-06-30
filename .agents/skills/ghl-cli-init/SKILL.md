---
name: ghl-cli-init
description: Initialize a reusable GoHighLevel CLI package for an agent or operator. Use when setting up this CLI for a new GHL location, discovering existing GHL pipelines/stages/tags before creating `.ghl-cli.json`, confirming inferred setup defaults with the user, generating safe examples for `ghl help agent`, instructing the user to create `.env` from `.env.example` when needed, or onboarding another agent to use the GHL CLI without hardcoded customer or home-directory details.
---

# GHL CLI Init

Use this skill to configure the reusable GHL CLI for a user's account without storing secrets.

## Security Rules

- Never ask the user to paste the private integration token into chat.
- Never print `GHL_PRIVATE_INTEGRATION_TOKEN`, full env, auth headers, or raw request headers.
- Never write real tokens into `.ghl-cli.json`, `.env`, README, `AGENTS.md`, or logs.
- Do not create extra env example files during init. If `.env` is missing, tell the user to copy `.env.example` to `.env` and set real values outside chat.
- Do not enable or recommend outbound messaging, payments, social posting, or GHL automations unless the user explicitly asks to extend the CLI.

## Workflow

1. Locate the CLI.
   - If in the repo root, use `./bin/ghl`.
   - If installed on `PATH`, use `ghl`.
   - If unsure, run `pwd`, `ls`, and `test -x ./bin/ghl`.

2. Read local instructions.
   - Read `AGENTS.md`.
   - Read `README.md` only if setup URL, permissions, or environment details are needed.

3. Discover what GHL already has.
   - Run `./bin/ghl doctor` first.
   - If env vars are present and API is reachable, run:

```sh
./bin/ghl pipelines
./bin/ghl tags list
```

   - Use the returned pipeline names, stage names, and tag names as proposed defaults.
   - If exactly one pipeline exists, propose that pipeline as the default.
   - If multiple pipelines exist, propose the current configured pipeline when present; otherwise ask the user which pipeline should be default.
   - For stages, use the selected pipeline's stage order.
   - For tags, propose active tags that look CRM-safe. Include common CRM tags such as `client`, `prospect`, `partner`, `do-not-contact`, and `needs-cleanup` when present. Do not invent customer-specific tags.
   - Do not inspect or print contact/opportunity records merely to create help examples. Use generic examples unless the user explicitly asks for account-specific examples.

4. Ask the user to confirm or clarify.
   - Present the proposed config in a short block:
     - Default pipeline.
     - Pipeline stages.
     - Canonical tags.
     - Generic help examples.
   - Ask only for unresolved choices or approval to proceed.
   - If GHL cannot be reached, ask for the missing setup details manually.
   - Manual fallback questions:
     - Default pipeline name.
     - Pipeline stage names in order.
     - Canonical tag names.
     - Example company name for help output.
     - Example contact name and email for help output.
     - Example opportunity name.
   - Do not ask for the private integration token.

5. Run `ghl init`.
   - Use flags when the answers are already known.
   - Use interactive mode when running in a real terminal and questions remain.
   - Example:

```sh
./bin/ghl init \
  --default-pipeline "Sales Pipeline" \
  --stages "New Lead,Engaged,Qualified,Meeting Booked,Discovery Complete,Proposal Sent,Nurture" \
  --tags "client,prospect,partner,do-not-contact,needs-cleanup" \
  --example-company "Example Co" \
  --example-contact "Jane Doe" \
  --example-email "jane@example.com"
```

6. Verify generated files.
   - Confirm `.ghl-cli.json` exists.
   - If `.env` is missing, instruct the user to copy `.env.example` to `.env` and set real values outside chat.
   - Confirm `.ghl-cli.json` does not contain a real token.
   - Run:

```sh
./bin/ghl help agent
```

7. Guide token setup.
   - Point the user to:

```text
https://app.gohighlevel.com/v2/location/<locationId>/settings/private-integrations
```

   - Tell them to replace `<locationId>` with their real location id.
   - Tell them to set `GHL_LOCATION_ID` and `GHL_PRIVATE_INTEGRATION_TOKEN` outside chat, either in their shell or in the repo-root `.env` file.
   - The CLI automatically reads the repo-root `.env` file.
   - Recommend the README's least-privilege permissions.

8. Verify live connectivity only after the user confirms env vars are set.
   - Run:

```sh
./bin/ghl doctor
```

   - Report only presence/reachability/status, never token values.

## Output Shape

After setup, report:

- What was discovered from GHL.
- What the user confirmed or changed.
- Config file created or updated.
- Whether `.env` exists or the user still needs to create it from `.env.example`.
- Default pipeline.
- Stage list.
- Tag list.
- Whether `ghl help agent` reflects the user's examples.
- Whether `ghl doctor` was run and whether API is reachable.

End with the next concrete action, usually setting env vars or running `ghl doctor`.
