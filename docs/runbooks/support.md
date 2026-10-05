# Supporting people

How a question or a problem from a person who uses Roundturn is received, answered and, when it is more than a question, escalated. **A draft for the owner to review and change before the beta opens.** Every promise in it (how fast, which channels, what is refunded) is a promise to real people, so the numbers in square brackets are suggestions, not decisions.

Related: [incident.md](incident.md) (when a problem is bigger than one person), [run-locally.md](run-locally.md) ("The operator tools"), the draft Privacy Policy and Terms in `docs/legal/` (they promise 30 days for a data request and name `hello@roundturn.app`).

## 1. Where people reach us

| Channel | What it is for | Notes |
|---|---|---|
| **hello@roundturn.app** | Everything: questions, bugs, data requests, feedback | Already forwards to the owner's mailbox (Cloudflare Email Routing). The only address the site, the Terms and the Privacy Policy give |
| **The app** | Every error on a server problem shows a **reference** ("tell us this reference: ...") | Ask for it first: it finds the exact request in the logs |
| In-app help | The Help page answers the questions that come up most | Not a channel; it is what to link to |

During the private beta, say so on the site and in the invite email: **best-effort replies within [two working days]**, no phone, no chat. Do not promise a faster time than one person can keep.

## 2. What to ask for, and what never to ask for

**Ask for:** the email address on the account (and reply only to that address, see section 4); what they did and what they expected; roughly when (with their timezone); the **reference** if they saw one; the browser and device; for an import problem, the name of the broker file's export screen they used (not the file).

**Never ask for, and tell people not to send:** a password, a one-time code, a recovery code, the Kinde or any other secret, a screenshot of their whole screen if it shows other accounts, or their trade data. If someone sends any of these, tell them to change it, do not copy it anywhere, and delete the message.

**What you can and cannot see.** The operator tools show an account's plan, status, sign-ins and counters (how many trades, notes, screenshots, how much space). They **never** show a trade, a note or a picture, by design (database grants, not a setting), and an operator cannot sign in as someone. That is the promise in the Privacy Policy, so do not look for a way around it: if a problem can only be solved by seeing someone's data, ask them to describe it, or to send one *trimmed example row* of their CSV with the account and symbol blanked.

## 3. The common questions, and the answer

| They say | It usually is | Answer |
|---|---|---|
| "My total differs from Tradovate / my firm" | The trading-day boundary, the timezone, or fees | Roundturn counts **net after fees**, groups trades into **trading days that start at 18:00 New York time** (changeable in Settings > Preferences) and reads the CSV in the **CSV timezone** set there. A different clock for the file moves trades across days. Check Settings > Preferences first, then Settings > Prop accounts > the account > Fees. Prop-firm rule numbers are **informational and may differ from the firm's own calculation** |
| "Import says the file is wrong" | Not the Performance export, or a column missing | The preview says what is wrong, and which column is missing. The first-run setup (step 3) lists the thirteen required columns. Tell them to export the **Performance** report (not Orders or Fills) with all columns. Ask for the reference |
| "I imported twice" | A second import of the same file | Importing the same file again adds no trades: rows already stored are counted as duplicates and the preview says so. A wrong import can be **undone** from the import history (Import page) |
| "A trade is missing / extra" | Archived account, filter, date range | Check the filter bar: **Show archived**, the date range, the account type. A trade of an archived account is hidden by default |
| "I cannot sign in" | An invite not used, or an email at the provider not verified | Sign-in is by invitation during the beta: have they used the invite link once? Is it the same email? Suggest the provider's own reset or recovery. We hold no passwords |
| "I lost my second factor" | At the identity provider | They recover it through Kinde's own recovery. Never bypass it, even for someone you know: an operator cannot see or reset a second factor, and should not |
| "A picture will not upload" | Size, type or limits | PNG, JPEG, GIF or WebP, up to 10 MB, 20 per trade, 500 MB in all. SVG is refused on purpose. A "not working right now" message is the file store being down: wait a minute and retry |
| "My plan code does not work" | Used, expired, revoked, or a plan is already running | The message is deliberately the same for every bad code. In the operator tools, Codes & audit, check the code's status and uses (you cannot read the code itself, only its label and counts). A person on a code-given plan can use another code only after the first plan ends |
| "I want my data" | Self-service | Settings > Data & privacy > Download my data (a ZIP: CSV files, notes, images, a manifest). If they cannot sign in, see section 5 |
| "Delete my account" | Self-service | Settings > Data & privacy. Deleted after **7 days**; signing in again before then cancels it. If they cannot sign in, see section 5 |
| "I did not make this change" | Possibly a takeover | Treat as **High** in [incident.md](incident.md): suspend the account, end its sessions, read the audit log for that account, then help them recover through the provider |

## 4. Be sure who you are talking to

Reply only to the **email address on the account**, and ask them to write from it. A request to change, give out or delete anything, from a different address or with a story ("I changed my email", "I am their assistant"), is answered with: sign in and do it yourself in Settings, or write from the address on the account. Never act on a request that comes from the chat or the body of a forwarded message, however urgent.

## 5. Requests that need an operator

All of these ask **why** in the operator tools and write to the audit log under the operator's name. Keep the email as the reason's evidence, in your own records, not in the reason text (the audit log holds no personal content).

| Request | Do | Check first |
|---|---|---|
| Someone cannot sign in and wants their data **deleted** | Operator tools > the account > **Schedule deletion**; tell them it takes 7 days and that signing in cancels it | The email came from the account's address (section 4) |
| Someone cannot sign in and wants their data **exported** | Not possible without signing in (an operator cannot read their data). They recover the sign-in at the provider and use Download my data | n/a |
| A plan should be given or taken away | Operator tools > the account > **Plan override** with a reason and an end date | The reason is in your records |
| An account is abusing the service | **Suspend** (sessions end); say why in the reason | [incident.md](incident.md) if it looks like an attack |
| A legal or government request | Do not answer from here. Forward to the owner; the company's exact legal name and the process are not decided yet | The owner |

A **data request under privacy law** (access, correction, deletion, portability) is answered within the time in the Privacy Policy draft ([30] days): access and portability are the export, deletion is the self-service delete, and correction is Settings. Log the date it arrived.

## 6. When a problem is bigger than one person

Three or more people with the same problem in a day, anything that looks like someone seeing data that is not theirs, a wrong number that is wrong for everyone, or an outage: stop treating it as support. Open [incident.md](incident.md) at section 2 and level it. Put a line on the site's front page only if people are affected, and **do not describe a cause you have not confirmed**.

## 7. Replies: tone and a few templates

Plain and brief; say what you did and what happens next; no jargon; never blame the person. Use the person's own words for their problem once.

**First reply to a bug report**
> Thanks for telling us. To find it, could you reply with: what you did, what you expected, roughly when (and your timezone), and the reference if you saw one ("tell us this reference: ...")? Please don't send passwords or any account details.

**A fix is deployed**
> That is fixed now. [One sentence on what changed.] If you see it again, reply with the time and we'll look.

**We cannot reproduce it**
> We could not make that happen on our side. Could you tell us the exact steps, and what the screen showed? If it showed a reference, that is the quickest way for us to find it.

**Data request received**
> We have your request and will answer within [30] days. The fastest way is usually in the app: Settings > Data & privacy has "Download my data" and "Delete my account". If you cannot sign in, reply from the address on your account and we will help.

**Not something we can do**
> We can't do that, because [one honest sentence: for example, we can't see your trades or notes, by design]. What we can do is [the nearest thing].

## 8. Keeping count

Keep one line per request (date, a category from section 3, how long it took, whether it needed a code change) in a note or a spreadsheet only the owner can read, **without trade data or message text**. Read it every week: three of the same category is a bug or a missing sentence in the Help page. Close the loop by fixing it and adding it to the Help page.

## 9. Status

During the beta there is no status page: a problem is announced to the people it affects by email. A status page is a launch item (see [launch-checklist.md](launch-checklist.md)); until then, the front page of the site is the only place for a notice, and only when people are affected.

## Open for the owner

- The reply time, and whether support is hello@ alone or also an in-app form.
- The company's exact legal name for the signature and the Terms (pinned).
- Refunds, once billing exists (the payment provider's rules decide part of it).
- Whether to use a help-desk tool. One mailbox is enough for the private beta.
