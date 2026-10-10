# Legal drafts

**Drafts only: not legal advice, not reviewed by a lawyer, and not published anywhere.** They live in `docs/`, not in `site/`, because the marketing site deploys whatever is merged to `main`; nothing here is reachable from the internet. They exist so a professional has something concrete to correct (launch gate in `docs/design/04-security.md` section 4: "Privacy Policy, Terms, financial disclaimer, subprocessor list reviewed by a professional").

| File | What it is |
|---|---|
| `terms-of-service-draft.md` | Terms of Service for the private beta and, with section 8 completed, the paid plans |
| `privacy-policy-draft.md` | The full Privacy Policy that replaces the interim notice (beta form and accounts) at roundturn.app/privacy/ once the app opens widely |

Both end with a list of questions for the reviewing professional.

## What only the owner can fill in

1. **Operator's legal name and address** (the "operated by" line). The owner said (2026-10-04) that "Northfield Labs" is a business name; the exact legal wording, whether it is registered, and in which state or country, are still needed.
2. **Governing law and venue** for the Terms.
3. **Plans and payment wording** (Terms section 8) once a payment provider is chosen and has answered the pre-clearance emails.
4. **Retention periods**: deletion grace period, backup retention, security-record retention (set by the backup and deletion design).
5. **Subprocessor choices**: host, database, email-sending provider, payment provider, error tracking (the Privacy Policy table marks each *in use*, *planned* or *undecided*).
6. **Postal address**, if the reviewing professional says one is required.

## Keep in step with the product

- The Privacy Policy says things the product must then do: export, deletion, no trade content in logs or admin tools, hashed IP addresses, essential-only cookies. Each is a launch-gate item in doc 04; tick them off against the policy before publishing.
- The Terms' financial disclaimer and "not investment advice" wording match the marketing site's About page and what the payment providers are told (doc 08). Change them together.
- When published, convert to pages on the marketing site (`site/public/terms/` and a rewritten `site/public/privacy/`), update the invite page's links, and delete these drafts or mark them superseded.
