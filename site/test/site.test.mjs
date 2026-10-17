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
  assert.ok(pages.length >= 4, `found only ${pages.length} pages`);
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

test("the home page has no form of its own: it sends people to the app for the beta form, sign-in and sign-up", () => {
  const html = pageNamed("/index.html");
  assert.ok(!/<form[\s>]/i.test(html), "the site has a form");
  assert.ok(!/<input[\s>]/i.test(html), "the site has an input");
  // Each link is an anchor to the app's address whose visible text starts with the words people look for.
  const anchors = [...html.matchAll(/<a\b([^>]*)>([^<]*)<\/a>/g)].map((m) => ({ attrs: m[1], text: m[2] }));
  for (const [target, text] of [
    ["https://app.roundturn.app/join", "Sign up for the beta"],
    ["https://app.roundturn.app/signin", "Sign in"],
    ["https://app.roundturn.app/signup", "Sign up here"],
  ]) {
    const found = anchors.find((a) => a.attrs.includes(`href="${target}"`) && a.text.trim() === text);
    assert.ok(found, `no "${text}" link to ${target}`);
    assert.ok(found.attrs.includes('rel="noopener noreferrer"'), `${target} has no rel`);
  }
});

test("the home page tells people who it is for, what it does, where it falls short and where it is going", () => {
  const html = pageNamed("/index.html");
  for (const heading of ["Who it is for", "What it does", "Where it falls short today", "Where it is going"]) {
    assert.ok(html.includes(`>${heading}</h2>`), `no "${heading}" section`);
  }
  assert.match(html, /Tradovate auto-sync/);
  assert.match(html, /not built yet, and there is no date/);
});

test("every page links to the app only at its own address, over https", () => {
  for (const page of pages) {
    for (const [, url] of read(page).matchAll(/href="(https?:\/\/[^"]*roundturn[^"]*)"/g)) {
      assert.match(url, /^https:\/\/app\.roundturn\.app\/(?:join|signin|signup)$/, `${urlOf(page)} links to ${url}`);
    }
  }
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
  for (const text of [
    "your first name, your email address, the prop firm you named, and when you joined",
    "one-way hash",
    "does not store your ip address",
    "cloudflare",
    "render",
    "resend",
    "deleted automatically 24 hours after the code was made",
    "northfield labs, llc, a california limited liability company",
    "kinde, which stores that data in oregon, in the united states",
    "keyed hash of your ip address",
    "deleting takes effect 7 days after you ask",
    "recovery copies for up to 7 days",
    "copied once a day to a second private bucket",
    "stays in that copy for up to 30 days",
    "no period for deleting them has been set yet",
    "within 30 days",
    "18 and over",
  ]) {
    assert.ok(privacy.includes(text), `the privacy page is missing: ${text}`);
  }
  // The site itself runs no script and stores nothing, so what it says about cookies and tracking holds.
  for (const page of pages) assert.ok(!/<script/i.test(read(page)), `${urlOf(page)} loads a script`);
  assert.ok(!files.some((f) => f.endsWith(".js")), "there is a script in public/");
  const worker = readFileSync(new URL("../src/worker.js", import.meta.url), "utf8");
  assert.ok(!/set-cookie|env\.DB|\.prepare\(|cf-connecting-ip|user-agent|referer/i.test(worker.replace(/\/\/.*$/gm, "")), "the Worker stores or reads something the page says it does not");
});

test("the headers file carries a strict CSP and the other protections", () => {
  const headers = read(join(PUBLIC, "_headers"));
  const csp = headers.match(/Content-Security-Policy:\s*(.+)/)?.[1] ?? "";
  assert.ok(csp, "no CSP");
  assert.ok(!/unsafe-inline|unsafe-eval|\*|https?:|data:/.test(csp), `CSP is loose: ${csp}`);
  for (const directive of ["default-src 'none'", "script-src 'self'", "style-src 'self'", "font-src 'self'", "form-action 'self'", "base-uri 'none'", "frame-ancestors 'none'"]) {
    assert.ok(csp.includes(directive), `CSP lacks ${directive}`);
  }
  for (const header of ["X-Content-Type-Options: nosniff", "Referrer-Policy: no-referrer", "Strict-Transport-Security:", "Permissions-Policy:"]) {
    assert.ok(headers.includes(header), `missing ${header}`);
  }
});

test("the stylesheet makes no outside requests, and its fonts are files in /fonts/ that exist", () => {
  const css = read(join(PUBLIC, "style.css"));
  assert.ok(!/@import/i.test(css));
  const urls = [...css.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/gi)].map((m) => m[1]);
  assert.ok(urls.length >= 5, "no @font-face sources");
  for (const url of urls) {
    assert.match(url, /^\/fonts\/[a-z0-9-]+\.woff2$/, `the stylesheet loads ${url}`);
    assert.ok(existsSync(join(PUBLIC, url)), `${url} is missing`);
  }
});

test("the contact address is the one the site says it uses", () => {
  const addresses = new Set(pages.flatMap((p) => [...read(p).matchAll(/mailto:([^"]+)"/g)].map((m) => m[1])));
  assert.deepEqual([...addresses], ["hello@roundturn.app"]);
});

test("the terms and refund pages state the same price, trial and no-refund rule, and say paid plans are not open yet", () => {
  const terms = pageNamed("/terms/index.html").toLowerCase();
  const refunds = pageNamed("/refunds/index.html").toLowerCase();
  const home = pageNamed("/index.html").toLowerCase();
  for (const [name, text] of [["terms", terms], ["refunds", refunds], ["home", home]]) {
    assert.ok(text.includes("usd 15 per month"), `${name} does not give the price`);
    assert.ok(text.includes("7-day trial"), `${name} does not mention the trial`);
    assert.ok(text.includes("paid plans are not open yet"), `${name} does not say paid plans are not open`);
  }
  for (const text of ["payments are final and non-refundable", "we do not issue refunds", "there is no free plan"]) {
    assert.ok(terms.includes(text) || refunds.includes(text), `neither page says: ${text}`);
  }
  assert.ok(refunds.includes("we do not issue refunds"));
  assert.ok(refunds.includes("cannot be waived"), "the refund page does not keep legally required rights");
  assert.ok(terms.includes("northfield labs, llc, a california limited liability company"));
  assert.ok(terms.includes("not investment advice") && terms.includes("substantial risk of loss"));
  assert.ok(terms.includes("not affiliated with"));
});

test("every page links to the terms, privacy and refund pages in its footer", () => {
  for (const page of pages) {
    if (urlOf(page) === "/404.html") continue;
    const html = read(page);
    const footer = html.slice(html.indexOf("<footer"));
    for (const target of ["/privacy/", "/terms/", "/refunds/"]) {
      assert.ok(footer.includes(`href="${target}"`), `${urlOf(page)} footer does not link to ${target}`);
    }
  }
});

test("the privacy page covers payments without claiming the site stores anything", () => {
  const privacy = pageNamed("/privacy/index.html").toLowerCase();
  assert.ok(privacy.includes("payment is taken by paddle, which acts as the seller of record"));
  assert.ok(privacy.includes("never sees or stores your card number"));
  assert.ok(privacy.includes("we give paddle your email address"));
  assert.ok(privacy.includes('href="https://www.paddle.com/legal/privacy"'));
  assert.ok(privacy.includes("billing records"));
});

test("the terms, privacy and refund pages name Paddle as the seller of record, and no page names another provider", () => {
  for (const name of ["/terms/index.html", "/privacy/index.html", "/refunds/index.html"]) {
    const text = pageNamed(name).toLowerCase();
    assert.ok(/paddle[^.]*seller of record/.test(text), `${name} does not name Paddle as the seller of record`);
  }
  const terms = pageNamed("/terms/index.html").toLowerCase();
  for (const entity of ["paddle.com inc.", "paddle.com (canada) ltd.", "paddle.com market limited"]) {
    assert.ok(terms.includes(entity), `the terms do not name ${entity}`);
  }
  assert.ok(terms.includes('href="https://www.paddle.com/legal/buyer-terms"'), "the terms do not link Paddle's buyer terms");
  assert.ok(
    pageNamed("/refunds/index.html").includes('href="https://www.paddle.com/legal/refund-policy"'),
    "the refund page does not link Paddle's refund policy",
  );
  for (const page of pages) {
    assert.ok(!/lemon squeezy|planned\)/i.test(read(page)), `${urlOf(page)} still names a planned provider`);
  }
});
