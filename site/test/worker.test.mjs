// Tests for the Worker's routing and for wrangler.jsonc, the file Cloudflare reads at deploy. Node's built-in runner.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import worker from "../src/worker.js";

const ORIGIN = "https://roundturn.app";
const SITE = fileURLToPath(new URL("../", import.meta.url));

class FakeDb {
  rows = new Map();
  prepare(sql) {
    const db = this;
    return {
      async run() {
        return { success: true };
      },
      bind(...args) {
        return {
          async run() {
            if (sql.startsWith("INSERT")) db.rows.set(args[0], args[1]);
            return { success: true };
          },
        };
      },
    };
  }
}

function envWith(db = new FakeDb()) {
  const served = [];
  return { DB: db, ASSETS: { fetch: async (request) => (served.push(request), new Response("a static file", { status: 200 })) }, served };
}

const request = (path, init = {}) => new Request(`${ORIGIN}${path}`, init);

test("POST /api/waitlist reaches the waitlist handler", async () => {
  const env = envWith();
  const response = await worker.fetch(request("/api/waitlist", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email: "a@example.com" }) }), env);
  assert.equal(response.status, 200);
  assert.deepEqual([...env.DB.rows.keys()], ["a@example.com"]);
  assert.equal(env.served.length, 0);
});

test("other methods on /api/waitlist get 405", async () => {
  for (const method of ["GET", "PUT", "DELETE", "HEAD"]) {
    const response = await worker.fetch(request("/api/waitlist", { method }), envWith());
    assert.equal(response.status, 405, method);
    assert.equal(response.headers.get("allow"), "POST");
  }
});

test("any other /api path is a 404 and never falls through to a static file", async () => {
  const env = envWith();
  for (const path of ["/api/", "/api/waitlist/", "/api/admin", "/api/waitlist.json"]) {
    const response = await worker.fetch(request(path), env);
    assert.equal(response.status, 404, path);
  }
  assert.equal(env.served.length, 0);
});

test("everything else is handed to the static assets untouched", async () => {
  const env = envWith();
  const incoming = request("/about/");
  const response = await worker.fetch(incoming, env);
  assert.equal(await response.text(), "a static file");
  assert.equal(env.served.length, 1);
  assert.equal(env.served[0], incoming);
});

// ---- wrangler.jsonc --------------------------------------------------------------------------------------------

function readConfig() {
  const text = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8");
  // JSON with comments: drop whole-line and trailing // comments that are not inside a string.
  const json = text
    .split(/\r?\n/)
    .map((line) => line.replace(/^\s*\/\/.*$/, "").replace(/\s\/\/\s.*$/, ""))
    .join("\n");
  return JSON.parse(json);
}

test("wrangler.jsonc names the Worker, its entry point and the assets folder, and they exist", () => {
  const config = readConfig();
  assert.equal(config.name, "roundturn");
  assert.ok(existsSync(SITE + config.main), `${config.main} is missing`);
  assert.ok(existsSync(SITE + config.assets.directory), "the assets directory is missing");
  assert.equal(config.assets.binding, "ASSETS");
  assert.match(config.compatibility_date, /^\d{4}-\d{2}-\d{2}$/);
});

test("the site is served only at its own domain: workers.dev and preview addresses stay off", () => {
  const config = readConfig();
  assert.equal(config.workers_dev, false);
  assert.equal(config.preview_urls, false);
});

test("only /api/* runs code; pages are served straight from the assets", () => {
  const { assets } = readConfig();
  assert.deepEqual(assets.run_worker_first, ["/api/*"]);
  assert.equal(assets.not_found_handling, "404-page");
  assert.ok(existsSync(SITE + "public/404.html"));
});

test("the waitlist database is bound as DB, with a real database id (not the placeholder)", () => {
  const { d1_databases: databases } = readConfig();
  assert.equal(databases.length, 1);
  assert.equal(databases[0].binding, "DB");
  assert.equal(databases[0].database_name, "roundturn-waitlist");
  assert.match(databases[0].database_id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/, "put the D1 database id in wrangler.jsonc before merging");
});

test("the config holds no secret: no token, key or password", () => {
  const text = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8");
  assert.ok(!/token|secret|password|api[_-]?key/i.test(text.replace(/\/\/.*$/gm, "")), "a secret-looking key in wrangler.jsonc");
});
