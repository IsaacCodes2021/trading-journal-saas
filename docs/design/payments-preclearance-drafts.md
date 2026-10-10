# Payment provider pre-clearance: email drafts (spike S5)

**Status:** drafts only. Nothing has been sent. These go out from the owner's new payments email account once the placeholders below are filled in. **Filled in 2026-10-03:** the product name (Roundturn), the domain (roundturn.app, registered 2026-10-03) and the seller (Northfield Labs, LLC, a California limited liability company, from the owner on 2026-10-10). **The site is live** (2026-10-03, checked from outside: HTTPS, strict headers, no cookies, a tested waitlist), so these can go out. The providers will open roundturn.app, and what they find matches what the emails say. Remaining placeholder: `[YOUR NAME]`, the sender's own name and role at the company. Source for the policies quoted: doc 08 section on payment providers (read 2026-10-02).

**Why ask before building:** Stripe prohibits "funded prop trading" and needs approval for investment and brokerage services plus a catch-all "other financial services". Paddle prohibits trading platforms and trading signals or strategies. Lemon Squeezy lists neither and tells sellers to ask support first. A trading journal that analyzes the user's own trades should be outside all of these, but it sits near enough that an approval after the product is built would be an expensive surprise.

**Placeholders to fill in** (these depend on the owner decisions in issue 12):
- `[YOUR NAME]`

**Keep the wording accurate.** Do not claim anything the product does not do. The facts below are true of the design (docs 01, 04, 06).

---

## Draft 1: Lemon Squeezy support

**Subject:** Pre-approval question: trade journal analytics software for futures traders

Hello,

I am planning to launch a subscription web application, Roundturn (roundturn.app), first as a free invite-only beta and later with paid plans, and would like to confirm it fits your acceptable-use policy before I build the billing integration.

**What it is.** A journal and analytics tool for futures traders. A user uploads the trade history files they export from their own brokerage platform (for example a CSV), and the app shows statistics on that history: profit and loss, win rate, drawdown, performance by day and by instrument, and lets them attach notes and screenshots to individual trades. It is software for reviewing your own past results.

**What it is not.**
- It does not place, route or execute trades, and it does not connect to a brokerage or exchange account.
- It never holds or asks for broker credentials, API keys, or any money other than the subscription fee.
- It does not give investment advice, trade signals, recommendations or price predictions, and it does not manage money for anyone.
- It is not a proprietary trading firm and does not sell funded accounts or evaluations. Some users trade accounts from prop firms, and the app can record the rules of those accounts for their own tracking; it is informational and states that it may differ from the firm's official numbers.

**Who sells it.** Northfield Labs, LLC, a California limited liability company in the United States. After the free beta the product will be billed as a monthly or annual software subscription; the price is not set yet.

Could you confirm that this type of product is acceptable to sell through Lemon Squeezy, and tell me if there is anything in the description, the marketing wording or the product that you would want changed? I am happy to share screenshots or a demo.

Thank you,
[YOUR NAME]
payments@roundturn.app

---

## Draft 2: Stripe support

**Subject:** Business eligibility question before applying: trade journal analytics software

Hello,

Before I apply for an account I would like to confirm that my product is an eligible business, because some financial categories on your restricted businesses list are close to it.

**The product.** Roundturn (roundturn.app) is a web application that analyzes trading history for futures traders, to be offered first as a free invite-only beta and later as a subscription. Users upload files they export from their own brokerage platform, and the app computes statistics on those past trades (profit and loss, win rate, drawdown, results by day and instrument) and lets them add notes and screenshots. It is a record-keeping and self-review tool.

**How it relates to your restricted list.**
- It is not funded prop trading. I do not operate a proprietary trading firm and do not sell evaluations or funded accounts.
- It is not an investment, brokerage or trading platform. It cannot place or route orders, does not connect to any brokerage or exchange, and never holds broker credentials.
- It does not provide investment advice, signals or recommendations, and it does not handle, hold or move customer funds. The only money involved is the customer's software subscription.
- Marketing and the product describe past results of the user's own data only, and carry a disclaimer that the output is not investment advice.

**The business.** Northfield Labs, LLC, a California limited liability company in the United States. Revenue will be a monthly or annual software subscription after the free beta; the price is not set yet.

Please let me know whether this business is eligible to use Stripe, whether it falls under the "other financial services" category that needs approval, and what information you would need from me. I can provide a product demo, screenshots, terms of service and the privacy policy when they are ready.

Thank you,
[YOUR NAME]
payments@roundturn.app

---

## After sending
- Record each reply (date, who answered, the wording) in issue 9 and in doc 08, and update the Notion task.
- Paddle: its policy prohibits trading platforms and trading signals, and it keeps discretion. It is not planned as a first choice; ask only if the two above decline.
- If any provider answers "not eligible", the fallback options are in doc 06 (ADR-007, billing) and doc 08, and the decision is the owner's.
