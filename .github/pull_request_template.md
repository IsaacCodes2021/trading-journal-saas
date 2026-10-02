## What and why

<!-- One or two sentences. Link the issue: Closes #123 -->

## Checks

- [ ] Tests added or updated, and they pass
- [ ] No secrets, real trade exports, or personal data in the diff
- [ ] SQL uses placeholders for every value (no string-built values)
- [ ] User-supplied text is escaped or sanitized wherever it is rendered
- [ ] Schema change? Added as a new numbered migration (never edit a shipped one)
- [ ] Behavior change visible to users? README or docs updated

<!-- Once multi-tenancy lands (M2), add: new tables have user_id + RLS + two-tenant test. -->
