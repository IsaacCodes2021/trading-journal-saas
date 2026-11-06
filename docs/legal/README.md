# Legal drafts

**Drafts only: not legal advice and not reviewed by a lawyer.** Since 2026-10-14 the Terms and a billing and refund policy are also published as pages on the marketing site (`site/public/terms/`, `site/public/refunds/`), next to the interim privacy page. The site pages are what visitors see; these files are the version to hand to a professional. Change them together. They exist so a professional has something concrete to correct (launch gate in `docs/design/04-security.md` section 4: "Privacy Policy, Terms, financial disclaimer, subprocessor list reviewed by a professional").

| File | What it is |
|---|---|
| `terms-of-service-draft.md` | Terms of Service for the private beta and, with section 8 completed, the paid plans |
| `privacy-policy-draft.md` | The full Privacy Policy that replaces the interim notice (beta form and accounts) at roundturn.app/privacy/ once the app opens widely |

Both end with a list of questions for the reviewing professional.

## What only the owner can fill in

1. **Operator's legal name and address** (the "operated by" line). Done 2026-10-10: Northfield Labs, LLC, a California limited liability company.
2. **Governing law and venue** for the Terms. Assumed California on 2026-10-14 (the operator is a California LLC); a professional should confirm.
3. **Plans and payment wording** (Terms section 7 on the site, section 8 here). Done 2026-10-14: USD 15 per month, no free plan, 7-day trial, no refunds. Done 2026-10-16: the pages name Paddle as seller of record (needed before Paddle's website review). Drafted 2026-10-24 on branch `site/legal-pages-stripe`, not published: the pages name Stripe through Link (Managed Payments, "Sold through Link") instead. Still to do: drop "Paid plans are not open yet" when billing goes live.
4. **Retention periods**: deletion grace period, backup retention, security-record retention (set by the backup and deletion design).
5. **Subprocessor choices**: host, database, email-sending provider, payment provider, error tracking (the Privacy Policy table marks each *in use*, *planned* or *undecided*).
6. **Postal address**: none is published, by the owner's choice (email contact only); the reviewing professional says whether that suffices.

## Keep in step with the product

- The Privacy Policy says things the product must then do: export, deletion, no trade content in logs or admin tools, hashed IP addresses, essential-only cookies. Each is a launch-gate item in doc 04; tick them off against the policy before publishing.
- The Terms' financial disclaimer and "not investment advice" wording match the marketing site's About page and what the payment providers are told (doc 08). Change them together.
- When published, convert to pages on the marketing site (`site/public/terms/` and a rewritten `site/public/privacy/`), update the invite page's links, and delete these drafts or mark them superseded.
