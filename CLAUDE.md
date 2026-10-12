# Roundturn (trading-journal-saas): rules for Claude Code sessions

**Read `docs/HANDOFF.md` first.** It says what is live, how to run and test things, what is open, and where each document is.

The owner's standing rules, short form (the reasons are in the handoff, section 3):

- Never add or change files in `C:\Users\Isaac\Desktop\Development\trading-journal` (the original local app). Work here.
- Never print or paste a secret. `.env.*` files are git-ignored; delete a temporary secret file once it has done its job.
- Merge a pull request only when the owner says so, with `gh pr merge N --merge`. A merge to `main` deploys to production.
- No production change without an explicit yes: migrations, host settings, jobs with `--apply`, writes to the production database.
  Read-only checks are fine.
- Do not poll CI; read it once per message. The red "Workers Builds: roundturn" check is a known false alarm.
- Ask before downloads, installs, outside messages, account creation or spending.
- Explain abbreviations in parentheses on every use. Say which Render service by type (Cron Job or Web Service).
- Never publish the owner's home or postal address. Public contact is hello@roundturn.app.
- Run the full backend suite before pushing backend changes; many files are CRLF, so edit with line endings preserved.
- Before new work: `git switch main && git pull`, then branch from `origin/main`. Do not commit the untracked
  `docs/prop-rules/` or `marketing/` folders unless the owner asks.
