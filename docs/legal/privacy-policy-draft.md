# Roundturn Privacy Policy

> **DRAFT. Not legal advice, not reviewed by a lawyer, not published.** An interim notice for the beta form and for accounts has been live at roundturn.app/privacy/ since 2026-10-10; this full policy replaces it once the app opens widely. It describes what the product does as of 2026-10-10. Everything in `[square brackets]` is a decision or a fact only the owner can supply. Rows marked *planned* or *undecided* in the lists below must be settled before this is published. See `README.md` in this folder.

**Last updated:** [date of publication]
**Responsible for your data (the "controller"):** Northfield Labs, LLC, a California limited liability company. No postal address is published, by the owner's choice; contact is by email.
**Contact for privacy questions and requests:** hello@roundturn.app

## 1. The short version

- Roundturn stores **your account details and the trading records and journal material you give it**, so it can show them back to you.
- It **does not sell your data, show ads, or track you** across other sites. The app has no advertising or analytics trackers.
- It uses a small number of **service providers** to run (listed in section 5), under contracts that limit what they may do with your data.
- You can **export** your data and **delete** your account yourself (section 8).
- Your trades, notes and screenshots are not visible to our administrator tools. [Confirm in the finished product.]

## 2. What we collect

**Account.** Your email address and the sign-in details our sign-in provider holds for you (for example a unique identifier, whether your email is verified, whether you use a second sign-in step). We do not see or store your password. If you sign in with another account (for example Google), we receive what that provider shares with us, normally your email and name.

**Settings.** Time zones, the start of your trading day, your theme, and similar choices.

**Trading records you import.** The contents of the files you upload (for example a Tradovate Performance export): contracts, quantities, prices, times, profit and loss, and the identifiers in the file. We store these as trades and fills in your journal. We also keep a record of each import (file name, a fingerprint of the file, when, and which timezone it was read in) so an import can be undone.

**Journal material you write.** Notes, tags, screenshots you upload, and the labels and groups you create. Screenshots can contain anything you capture, so please crop out what you do not want stored.

**Accounts and money you record.** The accounts you create (name, firm, size, stage, rules and fees) and the costs and payouts you enter in the ledger.

**Plan and billing.** Which plan you have and until when, and codes you redeem. [When paid plans open: payment details are handled by Stripe through Link (Sold through Link, LLC, a Stripe affiliate, is the merchant of record); we give Stripe your email address and our account number for you, and receive a customer reference, the plan, the status and the dates and amounts of payments, not your card number; the order details you give at checkout (name, billing country, any tax number) can be seen by us in our Stripe account.]

**Security and operations.** Records that keep the Service safe and working: sign-in and security events, import and export events, actions taken by an administrator on your account (with their reason), error reports, and server logs. We keep IP addresses only as a keyed hash (HMAC-SHA-256 with a secret key; the address itself is not stored). Each sign-in record also keeps the browser description that your browser sends, cut to 300 characters. We **do not** put your trades, notes or screenshot names in logs or error reports.

**Support and the beta sign-up.** If you email us, we keep the email and our replies. If you asked to join the beta on the sign-up form, we keep your email address, your first name, your favourite prop firm, when each personal code was made and whether the email carrying it was sent, and a one-way fingerprint of the code (never the code itself).

**What we do not collect.** We do not collect your broker or prop-firm passwords or API keys, and we do not connect to your brokerage accounts.

## 3. Why we use it, and on what basis

| Purpose | Data | Basis (for readers in the EU, UK and similar regions) |
|---|---|---|
| Provide the Service you asked for: sign you in, import, analyze and show your data | account, settings, trading records, journal material, accounts and ledger | Contract |
| Keep the Service secure, prevent abuse, fix problems | security and operations records | Legitimate interests |
| Take payment and keep tax records [when paid plans open] | plan and billing | Contract; legal obligation |
| Answer you | support emails | Legitimate interests; contract |
| Send you your beta code at your request, and tell you when your beta access has ended | beta sign-up (address, first name, favourite prop firm) | Steps at your request before a contract; legitimate interests [CONFIRM WITH COUNSEL] |
| Meet legal duties | what the law requires | Legal obligation |

We do not make decisions about you by automated means that have legal or similarly significant effects, and we do not use your data to train AI models.

## 4. Who sees your data inside the company

Only the operator, and only as needed to run and support the Service. Administrator tools show account details, plan, status and usage counts, **not** trades, notes or screenshots; opening a user's page in those tools is recorded in an audit log. [Confirm in the finished product.]

## 5. Service providers (subprocessors)

We use these providers to run Roundturn. Each handles data only on our instructions and under a contract. [Update this list as choices are made; keep it public.]

| Provider | What for | Status |
|---|---|---|
| Cloudflare | the marketing site, DNS, email forwarding, and private object storage (R2) for screenshots and their daily copy | in use |
| Render | runs the application and hosts its Postgres database (Oregon, United States) | in use |
| Kinde | sign-in, second step, account identity; stores that data in Oregon, United States (owner, 2026-10-10) | in use |
| Resend (email sending) | the email with your beta code and the email that says your access has ended; the provider sees the recipient's address, your first name and the message | in use (domain verified 2026-10-10) |

**Region.** The application and its database run in the United States (Render, Oregon); screenshots are held by Cloudflare; sign-in data is held by Kinde in Oregon, United States. Add a payment provider and an error-tracking provider to the list above when they are in use (neither is today). Where data of people in the EU or UK is transferred to the United States, we rely on [the provider's standard contractual clauses or the Data Privacy Framework certification: CONFIRM PER PROVIDER].

We may also disclose data if the law requires it, to protect people's safety or our rights, or as part of a sale or reorganization of the Service (with the same protections).

## 6. Cookies and similar storage

Only what is essential:

- a **session cookie** that keeps you signed in (set by us, not readable by scripts, sent only to us);
- a short-lived **sign-in state** cookie while you are signing in;
- a few **browser storage** entries for your own preferences (for example your theme).

There are no advertising or analytics cookies, and no session recording. [Confirm with counsel whether a banner is needed for your user regions; essential-only storage often needs none.]

## 7. How long we keep it

- **Your journal and account:** until you delete it. Deleting takes effect 7 days after you ask (signing in again cancels it); the hourly cleanup then removes the journal, settings, screenshots, beta sign-up and codes, and asks the sign-in provider to remove the sign-in record, usually within an hour of the 7 days.
- **Backups:** the database host keeps recovery copies for up to 7 days (3 days on its Hobby workspace plan, 7 on Pro; its logical backups 7 days), so deleted data can remain in them until they age out. Screenshots are copied once a day to a second private bucket (Cloudflare R2, the same account), where a deleted picture can remain for up to 30 days.
- **Security and audit records:** kept after an account is deleted, detached from the person (the log is append-only), holding the kind of event, its date and the keyed IP hash, never the content of trades, notes or screenshots. No deletion period has been set (owner decision 2026-10-10: say so plainly).
- **Billing records:** as long as tax law requires. [When paid plans open.]
- **Support email:** [N months] after the conversation ends.
- **Beta sign-up:** if you made an account, until you delete the account (the sign-up and any codes made for your address are removed at the same moment; migration 0017). If you never made one, **24 hours** after your code was made (the owner's decision of 2026-10-10; a code works for 24 hours; removed by the hourly sweep, migrations 0017 and 0018). You can also ask us to remove it by email. Not reached by this: the host's database backups until they age out up to 7 days, the email provider's own record of messages it delivered [to be confirmed with the provider], and an identity started at the sign-in provider and never finished.
- **Earlier waitlist:** the database was deleted on 2026-10-10. It held one entry, nobody was invited from it, and nothing was copied elsewhere.

## 8. Your choices and rights

- **See and export** your data: from within the app you can download your journal (data as CSV files and your screenshots).
- **Correct** it: edit it in the app, or ask us.
- **Delete** it: delete your account in the app, or email us. You will be asked to confirm, and it cannot be undone after the 7-day grace period.
- **Object, restrict or withdraw consent** where the law gives you that right, and **complain** to your data-protection authority.
- **California and similar US state laws:** we do not sell or share personal information as those laws define them. You can ask to know, correct or delete what we hold.

To use any right, email hello@roundturn.app from the address on your account. We may need to confirm it is you. We reply within 30 days.

## 9. Security

We protect your data with, among other things: encryption in transit; a separate, isolated area for each person's data inside the database, enforced by the database itself; sign-in through a dedicated provider with a second step available; protected session cookies; limits on uploads and requests; backups; and a policy of keeping personal data out of logs and error reports. No system is perfectly secure. If a breach affects you, we will tell you and the authorities as the law requires.

## 10. Children

Roundturn is for people **18 and over**. We do not knowingly collect data from anyone younger; if we learn we have, we will delete it.

## 11. Changes

If we change this policy in a way that matters to you, we will tell you (for example by email or in the app) before it takes effect where we reasonably can. The date at the top shows the latest version.

## 12. Contact

**hello@roundturn.app**. [If you have an EU or UK representative or a data-protection officer, name them here.]

---

## Notes for the reviewing professional

1. **Controller and region.** The beta is operated by Northfield Labs, LLC, a California limited liability company (owner, 2026-10-10). No postal address is published, by the owner's choice: does an email contact suffice for the regions we expect, or is a mailbox address needed? US storage with GDPR-ready deletion and export is the stated design. Is a representative in the EU or UK needed at beta scale?
2. **Lawful bases** in section 3: confirm the table, in particular the "steps at your request before a contract" basis for the beta sign-up and legitimate interests for security records.
3. **Subprocessors and transfers** in section 5: confirm what contracts (data-processing agreements, standard contractual clauses) are needed per provider once chosen, and whether the list must be published as its own page.
4. **Retention periods** are placeholders; they depend on the backup and deletion design (point-in-time recovery window, soft-delete grace period) still to be set.
5. **Screenshots** can hold third-party or sensitive information. Is the warning in section 2 enough?
6. **Cookies.** The design uses only essential storage. Confirm no banner is needed in the regions we expect, and that the sign-in provider's own cookies on its pages are covered by its policy.
7. **IP addresses.** Hashed with a secret salt for abuse handling; is "shortened or hashed" accurate enough, and is a retention period needed?
8. **Breach notification** wording in section 9 against the law of the regions we expect.
9. **Sale of the service** wording in section 5.
10. **Children.** Confirm age 18 and the wording.
