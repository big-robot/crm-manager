# Security Policy

## Secrets

Do not commit GoHighLevel private integration tokens, `.env` files, API keys,
OAuth credentials, customer exports, backups, or live CRM data.

If a token is exposed:

1. Revoke or rotate it in GoHighLevel immediately.
2. Remove it from the affected system.
3. Audit recent CRM activity for unexpected writes.

## Reporting

Report security issues privately to the repository owner. Do not open public
issues containing tokens, credentials, customer data, or exploit details.

## CLI Scope

This CLI is intended for CRM hygiene: contacts, opportunities, pipelines, tags,
and custom fields. Do not add outbound messaging, payment, social posting, or
automation execution without a separate security review.
