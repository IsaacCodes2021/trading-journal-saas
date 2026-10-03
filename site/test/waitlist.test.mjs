// Tests for the waitlist function, run with Node's built-in runner (no dependencies): node --test test/
// The database is a small fake with the two calls the function uses, so these run anywhere.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { normalizeEmail, onRequest, onRequestPost } from "../functions/api/waitlist.js";

const ORIGIN = "https://roundturn.app";

class FakeDb {
  rows = new Map();
  statements = [];
  failSchemaTimes = 0;
  failInsert = false;

  prepare(sql) {
    this.statements.push(sql);
    const db = this;
    return {
      async run() {
        if (sql.startsWith("CREATE TABLE") && db.failSchemaTimes > 0) {
          db.failSchemaTimes -= 1;
          throw new Error("schema failed for someone@example.com");
        }
        return { success: true };
      },
      bind(...args) {
        db.lastBind = args;
        return {
          async run() {
            if (db.failInsert) throw new Error(`insert failed for ${args[0]}`);
            // Like the real table: the address is the primary key, so a plain INSERT of a duplicate fails.
            const duplicate = db.rows.has(args[0]);
            if (duplicate && !/OR IGNORE/i.test(sql)) throw new Error("UNIQUE constraint failed: waitlist.email");
            if (!duplicate) db.rows.set(args[0], args[1]);
            return { success: true };
          },
        };
      },
    };
  }
}

function post(body, { type = "application/json", headers = {} } = {}) {
  const payload = type === "application/json" && typeof body !== "string" ? JSON.stringify(body) : body;
  return new Request(`${ORIGIN}/api/waitlist`, { method: "POST", headers: { "content-type": type, ...headers }, body: payload });
}

const call = (request, db = new FakeDb()) => onRequestPost({ request, env: { DB: db } }).then((response) => ({ response, db }));

test("a valid address is stored lowercased and trimmed, with only the date", async () => {
  const { response, db } = await call(post({ email: "  Trader@Example.COM " }));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true });
  assert.deepEqual([...db.rows.keys()], ["trader@example.com"]);
  assert.match(db.rows.get("trader@example.com"), /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(db.lastBind.length, 2, "the insert binds the address and the date and nothing else");
});

test("a form post (no JavaScript) redirects to the thank-you page", async () => {
  const { response, db } = await call(post("email=a%40example.com&website=", { type: "application/x-www-form-urlencoded" }));
  assert.equal(response.status, 303);
  assert.equal(response.headers.get("location"), "/thanks/");
  assert.equal(db.rows.size, 1);
});

test("signing up twice, in any capitalisation, stores one row and answers the same way", async () => {
  const db = new FakeDb();
  const first = await call(post({ email: "a@example.com" }), db);
  const second = await call(post({ email: "A@Example.com" }), db);
  assert.equal(db.rows.size, 1);
  assert.deepEqual(await first.response.json(), await second.response.json());
  assert.equal(first.response.status, second.response.status);
});

test("invalid addresses are refused and nothing is stored", async () => {
  for (const email of ["", "nope", "a@b", "a b@example.com", "a@b.c", "x@example.com\nBcc: y@example.com", "a@example.com,b@example.com", "<a>@example.com", 42, null, undefined, ["a@example.com"]]) {
    const { response, db } = await call(post({ email }));
    assert.equal(response.status, 400, JSON.stringify(email));
    assert.equal(db.rows.size, 0, JSON.stringify(email));
  }
});

test("the honeypot field gets a success answer and stores nothing", async () => {
  const { response, db } = await call(post({ email: "bot@example.com", website: "http://spam.example" }));
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true });
  assert.equal(db.rows.size, 0);
  assert.equal(db.statements.length, 0, "the database is not even touched");
});

test("a request from another site is refused", async () => {
  for (const headers of [{ origin: "https://evil.example" }, { "sec-fetch-site": "cross-site" }, { origin: "http://roundturn.app" }]) {
    const { response, db } = await call(post({ email: "a@example.com" }, { headers }));
    assert.equal(response.status, 403, JSON.stringify(headers));
    assert.equal(db.rows.size, 0);
  }
  const { response } = await call(post({ email: "a@example.com" }, { headers: { origin: ORIGIN, "sec-fetch-site": "same-origin" } }));
  assert.equal(response.status, 200);
});

test("an oversized body is refused", async () => {
  const big = { email: "a@example.com", padding: "x".repeat(5000) };
  const { response } = await call(post(big));
  assert.equal(response.status, 413);
  const declared = await call(post({ email: "a@example.com" }, { headers: { "content-length": "999999" } }));
  assert.equal(declared.response.status, 413);
});

test("unsupported or broken bodies are refused", async () => {
  assert.equal((await call(post("email=a@example.com", { type: "text/plain" }))).response.status, 415);
  assert.equal((await call(post("{not json"))).response.status, 400);
  assert.equal((await call(post("[1,2,3]"))).response.status, 400);
  assert.equal((await call(post("null"))).response.status, 400);
});

test("a database failure is a generic 500 that leaks nothing, and the next request recovers", async () => {
  const db = new FakeDb();
  db.failSchemaTimes = 1;
  const failed = await call(post({ email: "someone@example.com" }), db);
  assert.equal(failed.response.status, 500);
  const text = JSON.stringify(await failed.response.json());
  assert.ok(!text.includes("someone@example.com") && !text.toLowerCase().includes("schema"), text);
  const retried = await call(post({ email: "someone@example.com" }), db);
  assert.equal(retried.response.status, 200);
  assert.equal(db.rows.size, 1);

  db.failInsert = true;
  const insertFailed = await call(post({ email: "other@example.com" }), db);
  assert.equal(insertFailed.response.status, 500);
  assert.ok(!JSON.stringify(await insertFailed.response.json()).includes("other@example.com"));
});

test("the table is created once per database, not on every request", async () => {
  const db = new FakeDb();
  await call(post({ email: "a@example.com" }), db);
  await call(post({ email: "b@example.com" }), db);
  assert.equal(db.statements.filter((sql) => sql.startsWith("CREATE TABLE")).length, 1);
});

test("other methods get 405 and an Allow header", () => {
  const response = onRequest();
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("allow"), "POST");
});

test("answers are never cached and are not sniffed", async () => {
  for (const request of [post({ email: "a@example.com" }), post({ email: "bad" }), post("email=a%40example.com", { type: "application/x-www-form-urlencoded" })]) {
    const { response } = await call(request);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  }
});

test("normalizeEmail accepts ordinary addresses and rejects the rest", () => {
  for (const good of ["a@example.com", "first.last+tag@sub.example.co.uk", "A@B.IO"]) assert.ok(normalizeEmail(good), good);
  for (const bad of ["", " ", "plain", "@example.com", "a@", "a@@example.com", "a@example", "a@example.c", "x".repeat(250) + "@e.com"]) assert.equal(normalizeEmail(bad), null, bad);
});

test("the function never logs", () => {
  const source = readFileSync(new URL("../functions/api/waitlist.js", import.meta.url), "utf8");
  assert.ok(!/console\./.test(source), "a console call could write an email address to the logs");
});
