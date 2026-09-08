# Require explicit Business reassignment

Business records can be listed, read, created, updated, and deleted independently of Contacts. Contact assignment and removal are separate operations, exposed as `contacts business assign` and `contacts business remove`, accepting repeated `--contact-id` flags.

Assigning a Contact that already belongs to another Business must be rejected unless `--replace` is supplied. The dry run shows the existing association. This deliberately requires more intent than a direct assignment request to protect existing CRM relationships from accidental replacement.

Assignment and removal commands accept at most 50 Contacts per invocation. Larger requests are rejected before any writes; the CLI does not split them into batches. This avoids partial completion across multiple requests. Larger changes require separate commands.

Removal requires the expected Business ID: `contacts business remove BUSINESS_ID --contact-id CONTACT_ID`. Reject the request if a Contact belongs to a different Business, so removal cannot silently clear an unrelated association.

Validate every Contact in an assignment or removal request before sending a write. If any Contact fails validation, stop the entire command and report the conflicts without changing any Contacts. This is a CLI preflight guarantee, not a guarantee of provider-side transaction atomicity.

Assignment also sets each Contact's `companyName` text field to the target Business's name. The real association and text field require separate API writes; report partial failures explicitly rather than presenting the operation as atomic or wholly successful.

Removal also clears the Contact's `companyName` only when it matches the expected Business's name. Preserve independently edited text that differs from that name. Report partial failures between association removal and text-field updates explicitly.

Automatic propagation of Business renames to associated Contacts' `companyName` fields is deferred. Renaming a Business does not trigger Contact updates in this release; existing real associations remain intact.
