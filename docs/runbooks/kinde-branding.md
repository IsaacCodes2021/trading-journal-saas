# Making Kinde's sign-in screens look like Roundturn

**Who does what:** you (the owner) in the Kinde and Cloudflare dashboards and in GitHub. The code is already written, in `kinde/` in this repository. Nothing in the app needs to change except one setting on Render, in step 2.

**Why this is a separate step.** The app has its own sign-in and sign-up pages (`/signin`, `/signup`, `/join`). They are the *first* screen. The password, the one-time code and the second factor are asked for by Kinde, on Kinde's page, and that cannot be moved into the app (Kinde does not allow entering credentials anywhere but its own domain). What can be done is to make Kinde's page look like ours. Until this is done, those later steps show Kinde's default look. Everything works either way.

**What Kinde requires** (from its documentation, checked 2026-10-10):

- Custom code runs **only on a custom domain** (for example `auth.roundturn.app`). Custom domains are free.
- The code comes from a **GitHub repository** you connect (Design, Custom code). Other Git hosts are not supported yet.
- Free and Pro plans have no code preview: try it in a non-production environment first. Plus and Scale plans can preview before it goes live.

## 1. Put the code in its own repository

`kinde.json` has to be at the root of the repository Kinde reads, so give it a small repository of its own rather than connecting this one.

1. GitHub: create a **private** repository, for example `roundturn-kinde-pages`.
2. Copy the *contents* of this repository's `kinde/` folder to its root, so it holds `kinde.json`, `package.json` and `kindeSrc/environment/pages/(kinde)/(default)/page.tsx`. (Folder names with parentheses are meant: Kinde reads them.)
3. Commit and push. Nothing needs installing on your machine: Kinde builds it.

What the code does: one page for every Kinde screen. It wraps Kinde's form in the same name, card and colours as the app's own pages (dark and light follow the visitor's setting), uses the IBM Plex fonts the marketing site already serves (`https://roundturn.app/fonts/...`; if Kinde's security policy blocks them the system font is used), and sets the six design properties Kinde documents (background, link colour, button colour and radius, input radius).

## 2. A custom domain for Kinde

1. Kinde: Settings, Environment, **Custom domain** (Kinde's guide is called "Pointing your domain"). Enter `auth.roundturn.app` and follow the prompts. Kinde shows a DNS record to add.
2. Cloudflare: DNS, add that record exactly as shown (a CNAME; set **DNS only**, the grey cloud, not proxied). Wait for Kinde to show it as verified.
3. **Then change one setting on Render**, in the same sitting: the web service's Environment, `TJ_KINDE_DOMAIN`, from `https://<tenant>.kinde.com` to `https://auth.roundturn.app`. The app checks that Kinde's discovery document names exactly this address as its issuer, so with the custom domain on and the old value in place, **sign-in fails** until the setting matches. Save, let Render redeploy, then sign in once to check.
4. In the Kinde application (the one named in `TJ_KINDE_CLIENT_ID`), the callback and logout addresses do not change: `https://app.roundturn.app/auth/callback` and `https://app.roundturn.app/invite`.

If sign-in breaks after step 3, put the old `TJ_KINDE_DOMAIN` value back and redeploy; the custom domain can stay.

## 3. Connect the repository

1. Kinde: Design, **Custom code**, connect a repo (this opens Settings, Git repo). Choose **Connect GitHub**, authorize it for **only** the `roundturn-kinde-pages` repository, then pick the repository and the branch.
2. Kinde's dashboard home shows a *code status*: open **View code status** if something is wrong. A design problem is listed separately from a sync problem.
3. Sign out of the app and sign in again. The email, code and password steps should now show the Roundturn name, card and colours.

## 4. The dashboard settings that still matter

These are the fallback if the code is switched off, and what Kinde uses for its emails:

- Design: brand colour `#6EA8FE` (dark) or `#1F5FD1` (light), the page title "Roundturn", and a logo if you have one.
- Do **not** turn off sign-up in Kinde. A new account is made *through* Kinde's sign-up, after the app has already checked the code; closing it there would stop every new person. The app refuses any account that arrives without a valid code, so Kinde's own sign-up page cannot be used to get in.

## If something looks wrong

| What you see | What to do |
|---|---|
| Kinde's default look is still there | Code status on the dashboard home: is the repository connected, the branch right, and is the custom domain verified and in use (`TJ_KINDE_DOMAIN`)? |
| The page is plain but the widget is unstyled or broken | The page must call `getKindeRequiredCSS()` and `getKindeRequiredJS()`; they are in `page.tsx`. Do not remove them. |
| Text is in the system font, not IBM Plex | Kinde's security policy blocked the font files. Harmless. Fonts can be hosted on a domain that matches the custom domain instead. |
| Sign-in returns to "We could not sign you in" after the domain change | `TJ_KINDE_DOMAIN` does not match Kinde's issuer. Put the exact address Kinde shows under the custom domain there. |

## Honest limits

- This code has **not been run**. It follows Kinde's documentation and has not been exercised against a tenant, because it needs the custom domain and the repository first. Expect to adjust it once you see it: the file is short and all the look is in one stylesheet at the top.
- Kinde's own emails (a one-time code, a password reset) keep Kinde's look unless a custom sender is set up in Kinde's dashboard. They are not part of this step.
