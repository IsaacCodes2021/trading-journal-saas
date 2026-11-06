// The reader of the pop-up's tallies (scripts/popup-stats.mjs): the question it asks, how it reports, and that it never
// shows the API token.
import assert from "node:assert/strict";
import test from "node:test";

import { DATASET, main, query, report } from "../scripts/popup-stats.mjs";

test("the question: one row per event over the last N days, from the dataset the Worker writes, and N must be a sane whole number", () => {
  assert.equal(
    query("30"),
    `SELECT index1 AS event, SUM(_sample_interval * double1) AS n FROM ${DATASET} WHERE timestamp > NOW() - INTERVAL '30' DAY GROUP BY event`,
  );
  for (const bad of ["0", "-1", "366", "abc", "", "1; DROP TABLE x", "7 OR 1=1"]) {
    assert.throws(() => query(bad), /--days/, bad);
  }
  assert.equal(DATASET, "roundturn_popup");
});

test("the report separates people who left from people who went on, as shares of those shown", () => {
  const rows = [
    { event: "shown", n: 200 },
    { event: "x", n: 80 },
    { event: "away", n: 50 },
    { event: "esc", n: 10 },
    { event: "copy", n: 12 },
    { event: "signup", n: 30 },
  ];
  const text = report(rows, 30);
  assert.match(text, /last 30 days/);
  assert.match(text, /Shown\s+200/);
  assert.match(text, /Closed with the X\s+80/);
  assert.match(text, /Clicked away\s+50/);
  assert.match(text, /Closed with Escape\s+10/);
  assert.match(text, /Left without acting\s+140\s+\(70\.0% of those shown\)/);
  assert.match(text, /Went on \(copied or signed up\)\s+42\s+\(21\.0% of those shown\)/);
});

test("nothing shown yet means n/a, not a division by zero; unknown events are named", () => {
  const text = report([{ event: "mystery", n: 3 }], 7);
  assert.match(text, /Shown\s+0/);
  assert.match(text, /n\/a/);
  assert.match(text, /Other events found: mystery/);
});

test("it asks Cloudflare's SQL API with the token, prints the report, and never prints the token", async () => {
  const calls = [];
  const lines = [];
  const fetchImpl = async (url, init) => (calls.push({ url, init }), { ok: true, json: async () => ({ data: [{ event: "shown", n: 4 }, { event: "x", n: 1 }] }) });
  await main(["--days", "7"], { CLOUDFLARE_ACCOUNT_ID: "acct1", CLOUDFLARE_API_TOKEN: "secret-token-value" }, fetchImpl, (line) => lines.push(line));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://api.cloudflare.com/client/v4/accounts/acct1/analytics_engine/sql");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers.authorization, "Bearer secret-token-value");
  assert.match(calls[0].init.body, /INTERVAL '7' DAY/);
  assert.match(lines.join("\n"), /Shown\s+4/);
  assert.ok(!lines.join("\n").includes("secret-token-value"));
});

test("missing settings and a refusal are explained without echoing the token", async () => {
  await assert.rejects(main([], {}, async () => ({}), () => {}), /CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN/);
  const refused = async () => ({ ok: false, status: 403 });
  await assert.rejects(
    main([], { CLOUDFLARE_ACCOUNT_ID: "a", CLOUDFLARE_API_TOKEN: "secret-token-value" }, refused, () => {}),
    (problem) => /403/.test(problem.message) && !problem.message.includes("secret-token-value"),
  );
  await assert.rejects(main(["--bogus", "1"], { CLOUDFLARE_ACCOUNT_ID: "a", CLOUDFLARE_API_TOKEN: "t" }, async () => ({}), () => {}), /unknown option/);
});
