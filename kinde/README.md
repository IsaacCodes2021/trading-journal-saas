# Roundturn's look for Kinde's sign-in screens

This folder holds the page code Kinde runs for its hosted screens (the one-time code, the password, the second factor). Kinde reads it from this repository:

- `kinde.json` at the repository root tells Kinde where the code is (`rootDir` is `kinde/kindeSrc`).
- `package.json` at the repository root only names `@kinde/infrastructure`, which the page code imports, so that Kinde can install it when it builds. Nothing else in the repository uses it.
- `kindeSrc/environment/pages/(kinde)/(default)/page.tsx` is the page. The folder names with parentheses are meant.

The steps to connect it, and what to do if something looks wrong, are in `docs/runbooks/kinde-branding.md`.
