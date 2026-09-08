# Recommend Business metadata through agent guidance

Before creating a Business, the agent should offer known website, phone, email, address, and description values with their sources, and ask whether the user has additional details. Missing values must never be invented. Put this guidance in `ghl help agent` and `AGENTS.md`.

This is agent guidance rather than an interactive CLI requirement. Name-only creation remains valid, with the sub-account supplied by the existing configuration, so scripts can create Business records without prompts or optional metadata.
