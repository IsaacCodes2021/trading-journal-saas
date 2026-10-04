# Incident response

What to do when something has gone wrong in production: a suspected break-in, leaked secret, bad deployment, data problem or outage. It is written for one operator who is awake and alone, and it uses only what exists today. Where a step depends on something that is not built or chosen yet (the host, monitoring, backups, a contact list), the step says so instead of pretending.

Related: [rotate-secrets.md](rotate-secrets.md) (every secret, how to replace it), [restore.md](restore.md) (getting data back), [run-locally.md](run-locally.md) (the operator tools), design doc 04 section 5 (what the alerts should be).

## Status of this runbook

- **Not yet rehearsed.** The design asks for one tabletop run before the beta opens (pick a scenario below, follow the steps without touching production, and write down every step that did not work). Nothing here has been used in a real incident.
- **Not yet verified against a database:** the "end every session" statement in section 3. Run it inside `BEGIN; ... ROLLBACK;` first and look at the row count before committing.
- **Open for the owner:** the contact list and the notice templates (section 7) are blank on purpose. The legal wording of the company name is still pending, and notices must use it.

## 1. The first fifteen minutes

1. **Write the time down** (UTC) and open a scratch note. Everything you do from here goes in it with a time. You will need it for the review and, if people's data was involved, for the notice.
2. **Decide how bad it is** (section 2). Do not investigate before this; the level decides whether you contain first or look first.
3. **Contain** (section 3) if the level is high or critical. Containment that can be undone is always better than waiting to be sure.
4. **Look** (section 4): the audit log first, then the host's logs.
5. **Fix and recover** (section 5), then **tell the people who need to know** (section 7), then **review** (section 8).

## 2. How bad is it

| Level | Means | Examples | Do |
|---|---|---|---|
| **Critical** | Someone other than the owner could have read or changed other people's trading data, or a credential that can is out | A tenant-isolation bug reported or proven; a database password or the owner role leaked; the host account taken over; an operator account taken over | Contain at once (section 3, everything that applies), then look |
| **High** | Accounts or the operator tools are at risk, but not other people's data | One person's account taken over; the identity provider's client secret leaked; the session secret leaked; abuse of invites or plan codes | Contain the part that is at risk, then look |
| **Medium** | The service is wrong or down for some people | A bad deployment; the database is unreachable (`/readyz` fails); a migration problem; sign-in failing | Roll back or fix; no notice unless it lasts |
| **Low** | A nuisance | One person cannot import a file; a plan code that does not work | Normal support |

When unsure between two levels, take the higher.

## 3. Containment: the levers that exist today

Everything here can be reversed. Prefer the narrowest lever that stops the problem.

**One person's account** (suspected takeover, abuse): open the operator tools (`/admin`, Users), find the person, then **Suspend** (the account stops working and their sessions end), or **End sessions** only. Every change asks for a reason and is written to the audit log with your name.

**Stop new people getting in:** in the operator tools open Codes & audit and **Revoke** every invite that still works (the overview tile "Invites · plan codes that work" says how many). Sign-up is invite-only unless `TJ_SIGNUP_MODE` was set to `open`; check that it was not. A revoked invite keeps the accounts it already created.

**Stop a plan code being used:** Codes & audit, Plan codes, **Revoke**. People who already used it keep what they got; take that back per person with a plan override if you must.

**Take an operator out:** as the schema owner, `python -m tradejournal.pg.users remove-operator --email <address>`. Their next request to the operator tools is refused. Do this first if an operator account may be taken over; then also end that person's sessions.

**End every session** (the whole product is signed out; people sign in again through the identity provider, which is quick and does not lose data). As the schema owner (`TJ_OWNER_URL`), inside a transaction you can check first:

```sql
BEGIN;
UPDATE sessions SET revoked_at = now() WHERE revoked_at IS NULL;
-- look at the row count, then COMMIT; (or ROLLBACK;)
```

A running instance keeps a session in a cache for about a minute, so a revoked session may still work on another instance for up to that long. Restarting the instances empties the cache at once.

**Stop sign-in entirely:** remove `TJ_KINDE_DOMAIN`, `TJ_KINDE_CLIENT_ID` and `TJ_KINDE_CLIENT_SECRET` from the host and restart: the sign-in routes then answer 503. **This does not end sessions that already exist**; do the statement above as well.

**Stop the whole service:** use the host's stop or suspend control. This is the lever for a Critical incident you cannot yet understand. The marketing site (`roundturn.app`) is a separate Cloudflare Worker and keeps working.

**Things that look like levers and are not:** `TJ_RATE_LIMITS=off` only turns the limits *off* (an emergency switch for a limit that is wrongly refusing people); there is no switch that makes them stricter. Suspending an account does not remove its data.

## 4. Looking: the audit log

The audit log is append-only for every role the application uses (a trigger refuses an update or a delete; only the schema owner may delete rows, to remove old ones past their retention, so **the owner's credential is the one that could rewrite history** and it must never sit on the host) and never holds a code, a token or a secret. Read it as the schema owner, or in the operator tools under Codes & audit (filter by what happened, who did it, how far back, or search by address or account id).

Useful questions, as the schema owner:

```sql
-- everything an operator did in the last day
SELECT id, created_at, action, actor_user_id, user_id, meta
FROM audit_log WHERE actor = 'admin' AND created_at > now() - interval '1 day' ORDER BY id DESC;

-- refused sign-ins and plan-code guesses in the last day
SELECT id, created_at, action, user_id, meta
FROM audit_log WHERE action IN ('login_failed', 'grant_refused') AND created_at > now() - interval '1 day' ORDER BY id DESC;

-- everything about one account
SELECT id, created_at, actor, action, meta FROM audit_log WHERE user_id = '<account id>' ORDER BY id DESC LIMIT 200;
```

What to look for: an operator action you did not take (only you should be an operator; `operator_granted` should appear only with `cli:owner` as the actor); a burst of `login_failed`; sessions from addresses (the log keeps only a keyed hash of the address, so you can tell two events came from the same place but not where it is); many `grant_refused` from one account (someone guessing codes; the limit is 10 an hour per person).

Then the host's logs for the same hours. In production the application writes one line of JSON per request (the method, the route, the status, the time and a request id; never the query string, headers, cookies or body) and, for an error, its type and where it happened, never its message. **A person who reports an error can quote the reference the page showed ("tell us this reference: ..."): search the log for that id to find the request and everything else written while it ran.** Application logs do not contain trade, note or screenshot content by design, and a scrubber removes anything shaped like a secret or an email address; if you find either in a log, that is itself a finding. (The structured log has been tested; its alerts and the host that keeps it have not been built yet.)

**What the operator tools cannot show you, by design:** a person's trades, notes or screenshots. If an investigation seems to need them, stop and decide with the owner whether to go to the database directly; that is a Critical-level step and it must be written in the incident note and disclosed.

## 5. Recovering

- **A bad deployment:** redeploy the previous image. Migrations are written to be additive, so the previous code runs against the newer schema; if a migration itself is the problem, see [restore.md](restore.md) before doing anything to the database.
- **A leaked or suspect secret:** [rotate-secrets.md](rotate-secrets.md). Rotate first, investigate second.
- **Lost or damaged data:** [restore.md](restore.md). Restore into a *new* database and compare; do not restore over the live one.
- **A taken-over account:** suspend, end sessions, then ask the person to secure their identity-provider account (set two-step verification) before restoring access with **Unsuspend**. If the plan or settings were changed, the audit log shows what.

## 6. If the owner's own access is the thing that is lost

Order of recovery: the email account (it receives every reset), then the identity provider (Kinde), the host, Cloudflare (the domain and the site), GitHub, the payment provider (when there is one). Two-step verification must be on for each; the recovery codes for each are kept offline, outside this repository. If an account cannot be recovered, assume everything it could do was done.

## 7. Telling people

**Not written yet, on purpose, and needs the owner:**

| Who | When | Template | Owner |
|---|---|---|---|
| People whose data was exposed | As soon as it is known, and within the shortest legal window that applies (hours to days in some places; the product is US-based but has people elsewhere) | blank | needs the company's exact legal wording first |
| The identity provider (Kinde) | If their service or our client secret is involved | blank | |
| The payment provider (when there is one) | If payment data or webhooks are involved | blank | |
| A public status note | If the service is down for more than an hour | blank | there is no status page yet (launch checklist task) |

A notice says what happened, what data was involved, what was done, what the person should do, and how to reach someone. It does not guess. It is sent from the product's own address, not a personal one.

## 8. After

Within a week, write a short review in the incident note: the timeline, what was affected, how it was found, what worked, what did not, and what changes (a test, a limit, an alert, a line in this file). Add the change as a task. Any step in this file that was wrong or missing gets fixed here the same day.

## 9. Scenarios to rehearse

Pick one before the beta opens and walk it through on paper, noting every place the runbook did not answer:

1. A tester reports that they can see another person's trade.
2. The Kinde client secret is pasted into a public place by mistake.
3. An operator account is signed in from somewhere you do not recognise.
4. A migration was applied that drops a column people use.
5. The database is unreachable and `/readyz` has failed for ten minutes.
