// Checks on the static site itself: nothing external, nothing inline, every link resolves, the disclaimers are there,
// and the CSP is as strict as the design says. Run with Node's built-in runner: node --test test/
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const PUBLIC = fileURLToPath(new URL("../public/", import.meta.url));

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

const files = walk(PUBLIC);
const pages = files.filter((f) => f.endsWith(".html"));
const urlOf = (file) => "/" + relative(PUBLIC, file).split(sep).join("/");
const read = (file) => readFileSync(file, "utf8");
const pageNamed = (url) => {
  const page = pages.find((p) => urlOf(p) === url);
  assert.ok(page, `no page at ${url}`);
  return read(page);
};

test("there are pages to check", () => {
  assert.ok(pages.length >= 5, `found only ${pages.length} pages`);
});

for (const page of pages) {
  const name = urlOf(page);
  const html = read(page);

  test(`${name}: has the basics (doctype, language, title, description, viewport)`, () => {
    assert.match(html, /^<!doctype html>/i);
    assert.match(html, /<html lang="en">/);
    assert.match(html, /<title>[^<]{5,}<\/title>/);
    assert.match(html, /<meta name="description" content="[^"]{20,}">/);
    assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1">/);
  });

  test(`${name}: no inline script, inline style or event handler (the CSP forbids them)`, () => {
    assert.ok(!/<script(?![^>]*\ssrc=)/i.test(html), "inline <script>");
    assert.ok(!/<style[\s>]/i.test(html), "<style> block");
    assert.ok(!/\sstyle\s*=/i.test(html), "style attribute");
    assert.ok(!/\son[a-z]+\s*=/i.test(html), "event handler attribute");
  });

  test(`${name}: loads nothing from another origin`, () => {
    const resources = [...html.matchAll(/<(?:script|img|link|iframe|source|audio|video|embed|object)\b[^>]*?\s(?:src|href|data)="([^"]+)"/gi)].map((m) => m[1]);
    for (const url of resources) assert.ok(url.startsWith("/") && !url.startsWith("//"), `${name} loads ${url}`);
    assert.ok(!/@import|url\(/i.test(html));
  });

  test(`${name}: every local link and asset exists`, () => {
    const targets = [...html.matchAll(/\s(?:src|href)="([^"#?]+)/g)].map((m) => m[1]).filter((u) => u.startsWith("/"));
    for (const target of targets) {
      const file = target.endsWith("/") ? join(PUBLIC, target, "index.html") : join(PUBLIC, target);
      assert.ok(existsSync(file), `${name} links to ${target}, which does not exist`);
    }
  });

  test(`${name}: external links are plain anchors that do not leak the referrer`, () => {
    for (const m of html.matchAll(/<a\b[^>]*href="(https?:[^"]+)"[^>]*>/g)) {
      assert.match(m[0], /rel="noopener noreferrer"/, m[1]);
    }
  });
}

test("the home page has the waitlist form, a honeypot, and posts to the function", () => {
  const html = pageNamed("/index.html").replace(/\r?\n/g, " ");
  assert.match(html, /<form id="join" class="join" action="\/api\/waitlist" method="post">/);
  assert.match(html, /name="email" type="email" required/);
  assert.match(html, /name="website" type="text" tabindex="-1" autocomplete="off"/);
});

test("the pages say what the payment providers and visitors need to be told", () => {
  const home = pageNamed("/index.html").toLowerCase();
  const about = pageNamed("/about/index.html").toLowerCase();
  for (const text of ["not investment advice", "substantial risk of loss", "not affiliated with tradovate", "free during the invite-only beta", "in development"]) {
    assert.ok(home.includes(text), `home page is missing: ${text}`);
  }
  for (const text of [
    "does not place, route or execute trades",
    "never asks for or holds broker passwords",
    "does not give investment advice, trade signals",
    "does not hold or move customer funds",
    "not a proprietary trading firm",
    "does not sell funded accounts",
    "past results are not indicative of future results",
    "may differ from the firm's official numbers",
    "not affiliated with, endorsed by or sponsored by tradovate",
  ]) {
    assert.ok(about.includes(text), `about page is missing: ${text}`);
  }
});

test("nothing on the site promises what the product does not do", () => {
  const all = pages.map((p) => read(p).toLowerCase()).join("\n");
  for (const forbidden of ["guarantee", "risk-free", "risk free", "make money", "profit from", "trading signals service", "get funded", "pass your evaluation", "beat the market"]) {
    assert.ok(!all.includes(forbidden), `the site says "${forbidden}"`);
  }
});

test("the privacy page matches what the code does", () => {
  const privacy = pageNamed("/privacy/index.html").toLowerCase();
  assert.ok(privacy.includes("sets no cookies") && privacy.includes("no analytics, advertising or tracking scripts"));
  assert.ok(privacy.includes("your email address and the date you joined"));
  const script = read(join(PUBLIC, "waitlist.js"));
  assert.ok(!/document\.cookie|localStorage|sessionStorage|indexedDB/.test(script), "the script stores something in the browser");
  assert.ok(!/fetch\(\s*["'`]https?:/.test(script), "the script calls another origin");
  const fn = readFileSync(new URL("../src/waitlist.js", import.meta.url), "utf8");
  assert.ok(!/set-cookie/i.test(fn), "the function sets a cookie");
  assert.ok(!/cf-connecting-ip|user-agent|referer/i.test(fn), "the function reads something the privacy page says it does not store");
});

test("the headers file carries a strict CSP and the other protections", () => {
  const headers = read(join(PUBLIC, "_headers"));
  const csp = headers.match(/Content-Security-Policy:\s*(.+)/)?.[1] ?? "";
  assert.ok(csp, "no CSP");
  assert.ok(!/unsafe-inline|unsafe-eval|\*|https?:|data:/.test(csp), `CSP is loose: ${csp}`);
  for (const directive of ["default-src 'none'", "script-src 'self'", "style-src 'self'", "form-action 'self'", "base-uri 'none'", "frame-ancestors 'none'"]) {
    assert.ok(csp.includes(directive), `CSP lacks ${directive}`);
  }
  for (const header of ["X-Content-Type-Options: nosniff", "Referrer-Policy: no-referrer", "Strict-Transport-Security:", "Permissions-Policy:"]) {
    assert.ok(headers.includes(header), `missing ${header}`);
  }
});

test("the stylesheet makes no outside requests", () => {
  assert.ok(!/@import|url\(/i.test(read(join(PUBLIC, "style.css"))));
});

test("the contact address is the one the site says it uses", () => {
  const addresses = new Set(pages.flatMap((p) => [...read(p).matchAll(/mailto:([^"]+)"/g)].map((m) => m[1])));
  assert.deepEqual([...addresses], ["hello@roundturn.app"]);
});
