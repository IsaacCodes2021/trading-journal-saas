# 08. Spike prep research (S1b, S5, S4)

**Status:** research notes, 2026-10-02. Web research only: no accounts were created, nothing was installed, no one was contacted. Vendor pages change; **re-check anything here at signup**. Facts below were read from the vendors' own pages unless marked *third-party* or *unverified*. Policy wording is paraphrased, not quoted.

## What this changes

1. **The free-tier premise was wrong in a way that matters.** Docs 03 and 06 said hosted identity providers advertise 25,000 free MAU. True for Auth0, but **Auth0's free plan has no MFA**, and MFA is mandatory for the admin (ADR-012) and a P1 feature for users. Clerk's MFA is also paid. MFA is the feature that decides.
2. **Front-runner for S1b: Kinde.** Its free plan has everything we need. Fallbacks: WorkOS AuthKit, Supabase Auth. Drop Auth0 and Clerk unless a paid plan is acceptable.
3. **The payment-provider risk is real but manageable.** Stripe prohibits "funded prop trading" and restricts investment services; Paddle prohibits trading platforms and trading signals or strategies and keeps discretion; Lemon Squeezy lists neither and tells sellers to ask first. Pre-clear in M1 and keep marketing wording careful.
4. **Render facts:** zero-downtime deploys are documented, and PostgreSQL 13 to 18 is offered with 18 the default. **Use PostgreSQL 18 locally**, not 17 as I first suggested, to match production.

## 1. S1b: identity providers

Requirements (doc 03 section 4, ADR-012): Google sign-in, email and password, TOTP MFA, email verification, custom domain, standard OIDC with PKCE, a management API that deletes users, user export, at least 5,000 MAU free, an MFA indicator in the ID token and step-up re-authentication.

| | Free plan (checked 2026-10-02) | TOTP MFA on free | Custom domain on free | MFA claim and step-up | Delete and export users | Verdict |
|---|---|---|---|---|---|---|
| **Kinde** | 10,500 MAU; first paid plan $25/month | **Yes** (authenticator app and email; 10 SMS included) | **Yes** | `acr_values`, `max_age`, `amr` and `auth_time` are described in a Kinde docs pull request that is **still open** ([PR 811](https://github.com/kinde-oss/documentation/pull/811)); whether the live service behaves that way is **unverified** | Management API: list, get, create, update, delete user; Python SDK; standard discovery document ([docs](https://docs.kinde.com/developer-tools/sdks/backend/python-sdk/)). Export: not checked | **Front-runner** |
| **WorkOS AuthKit** | First 1M MAU free | MFA is listed as included; the page does not say which plan | **No: $99/month** | `amr` and `auth_time` in tokens; `max_age` on the authorize endpoint ([WorkOS](https://workos.com/blog/step-up-authentication)) | `DELETE /user_management/users/{id}`; paginated list (max 100); password import yes, **hash export no** ([API](https://workos.com/docs/reference/authkit/user)) | Fallback. Unclear whether a standard OIDC client can integrate: the overview page does not say |
| **Supabase Auth** | 50,000 MAU; **free projects pause after a week of inactivity** | Basic MFA: yes | **No** (Pro: $10 per domain per month) | JWT carries `aal` (1 or 2) and `amr` ([docs](https://supabase.com/docs/guides/auth/jwt-fields)); also an OAuth 2.1 and OIDC server ([docs](https://supabase.com/docs/guides/auth/oauth-server/oauth-flows)) | Not checked | Fallback, but production needs Pro ($25/month) because of pausing |
| **Auth0** | 25,000 MAU | **No** (MFA starts on the $35/month tier for 500 MAU) | Yes (card verification) | Best documented: `amr` with `mfa`, `acr_values`, `max_age` ([docs](https://auth0.com/docs/secure/multi-factor-authentication/step-up-authentication/configure-step-up-authentication-for-web-apps)) | Management API (known; not rechecked) | Drop unless paying |
| **Clerk** | Counts monthly *retained* users; paid plans from $25/month | **No** (Pro and Business only) | Yes | Acts as an OIDC provider since Feb 2025 ([changelog](https://clerk.com/changelog/2025-02-13-clerk-oidc)); MFA claim and step-up not found | Not checked | Drop unless paying |

Sources for the plans: [Auth0](https://auth0.com/pricing), [Kinde](https://kinde.com/pricing/), [WorkOS](https://workos.com/pricing), [Supabase](https://supabase.com/pricing), [Clerk](https://clerk.com/pricing). One conflict: an earlier comparison article said Kinde's free tier is 25,000 MAU; Kinde's own page says **10,500**, which is what I used.

**What the S1b spike must prove against a live Kinde tenant** (this is where documentation and reality can differ):

- The ID token validates (signature through the JWKS, `iss`, `aud`, `exp`, `nonce`) and includes `email_verified`.
- After an authenticator-app login the token carries `amr` including `mfa`, and `auth_time`.
- A step-up request (`acr_values` and `max_age`) forces a fresh MFA challenge without destroying the session; the resulting token shows it.
- The delete-user and list-users management calls work with a machine-to-machine credential; find out what an export of users looks like.
- Free-plan limits that matter: custom domain with our email sending, rate limits, and what happens above 10,500 MAU.
- Fallback decision: if the live behavior does not match, try WorkOS AuthKit and test whether a standard OIDC client can use it.

## 2. S5: payment providers

Policies read on 2026-10-02. The product is trade analytics on the user's own history: no signals, no advice, no custody of money, not a prop firm.

| Provider | What the policy says that touches us | Reading | Path |
|---|---|---|---|
| **Stripe** ([restricted businesses](https://stripe.com/legal/restricted-businesses)) | Funded prop trading is **prohibited**. Investment and brokerage services, and a catch-all "other financial services", **need approval**. A Japan-specific entry prohibits advice on how to profit from trading | A journal is outside the named categories, but the catch-all and the prop-trading word are risks | Contact Stripe support with a precise description before building |
| **Paddle** ([what you can't sell](https://www.paddle.com/help/start/intro-to-paddle/what-am-i-not-allowed-to-sell-on-paddle)) | Prohibits regulated financial products, trading platforms and exchanges, investment or financial advice **including trading signals and strategies**, and business or investment opportunities. Reserves the right to judge borderline cases; no pre-clearance route is described | Probably outside, but the language is broad and the process is not forgiving | Lower priority |
| **Lemon Squeezy** ([prohibited products](https://docs.lemonsqueezy.com/help/getting-started/prohibited-products)) | Prohibits currency exchange and crypto or NFT products; says nothing about trading software or signals. **Tells sellers to ask support first when unsure** | Most explicit invitation to pre-clear. It is Stripe-owned since 2024 ([discussion](https://news.ycombinator.com/item?id=41080018)); its long-term status is **unverified** | Ask first |

**Recommendation.** In M1, email Lemon Squeezy and Stripe support with: what the product does, that it analyzes only data the user uploads, that it gives no advice or signals, holds no funds and connects to no broker, and that it is not a prop firm. Keep the marketing site to "analytics for your own trade history" and avoid describing anything we sell as funded prop trading, signals or strategies. Whether each provider's API can create discount codes (needed by the admin back end) is still **unverified**.

## 3. S4: Render

| Question | Finding |
|---|---|
| Zero-downtime deploys? | Yes. A new instance starts and the old one keeps serving until the new one is healthy, then traffic moves ([docs](https://render.com/docs/deploys)). Health checks are optional but recommended. **Adding a persistent disk disables this**, and we use none. Multiple instances roll one at a time |
| Graceful shutdown | The old instance gets SIGTERM, then SIGKILL after a shutdown delay (default 30 seconds, configurable to 300). The wording about a 60-second wait before SIGTERM is ambiguous; test it. Uvicorn must finish in-flight requests on SIGTERM |
| PostgreSQL versions | 13 to 18 fully supported; **18 is the default for new databases** ([changelog](https://render.com/changelog/postgresql-18-is-now-available-for-render-postgres-databases), [upgrading](https://render.com/docs/postgresql-upgrading)) |
| Instance types | Starter $7 (512 MB, 0.5 CPU) and Standard $25 (2 GB, 1 CPU), from third-party summaries; Render's own pricing page could not be read automatically. One article claims the "$7 plan really costs $13" (a workspace fee); **unverified**, so confirm what a workspace costs |
| PITR | From the earlier check: 3-day window on the Hobby workspace, 7-day on Pro |

What S4 must still measure: resident memory and p95 latency of the real image under load, the exact shutdown behavior with uvicorn, caching of `/assets/*` through Cloudflare, and the real monthly bill including any workspace fee.

## 4. Corrections applied elsewhere

README sources and unverified list; doc 02 cost table; doc 03 sections 4 and 5; doc 05 (S5 row); amendments under ADR-003, ADR-005 and ADR-007 in doc 06 (appended, nothing rewritten).
