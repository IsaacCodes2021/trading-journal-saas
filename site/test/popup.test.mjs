// The launch offer pop-up: its markup on the home page, and public/popup.js run against a small fake of the page (Node's
// vm, no browser), so each thing a visitor can do is checked: what is counted, how often, and that nothing breaks when the
// browser has no storage, no beacon or no clipboard.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const PUBLIC = fileURLToPath(new URL("../public/", import.meta.url));
const home = readFileSync(PUBLIC + "index.html", "utf8");
const code = readFileSync(PUBLIC + "popup.js", "utf8");
const SIGNUP = "https://app.roundturn.app/signup?promo=LAUNCH10";

// ---- the markup ---------------------------------------------------------------------------------------------------

test("the home page carries the launch offer: the code, the discount and its length, a sign-up link that carries the code, and a way to copy it", () => {
  const dialog = home.match(/<dialog class="offer" id="offer"[\s\S]*?<\/dialog>/)?.[0] ?? "";
  assert.ok(dialog, "no pop-up on the home page");
  const text = dialog.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
  for (const needed of ["Join today and get 10% off.", "LAUNCH10", "10% off Roundturn Pro for your first 3 months", "7-day trial", "no card", "New accounts only", "Sign up now", "Copy link"]) {
    assert.ok(text.includes(needed), `the pop-up does not say: ${needed}`);
  }
  assert.ok(dialog.includes(`data-offer="signup" href="${SIGNUP}" rel="noopener noreferrer"`), "the sign-up link is wrong");
  assert.ok(/<button type="button" class="offer-x" data-offer="x" aria-label="Close this offer">/.test(dialog), "no labelled close button");
  assert.ok(/<button type="button" class="btn btn-big btn-quiet" data-offer="copy">Copy link<\/button>/.test(dialog), "no copy button");
  assert.ok(dialog.includes('role="status" data-offer-status'), "no status line for the copy");
  assert.ok(dialog.includes('href="/terms/"') && dialog.includes('href="/refunds/"'), "the offer does not link the terms and the refund policy");
  for (const id of ["offer-title", "offer-text"]) assert.ok(dialog.includes(`id="${id}"`), `${id} is missing`);
  assert.ok(dialog.includes('aria-labelledby="offer-title"') && dialog.includes('aria-describedby="offer-text"'));
  assert.ok(!/<form[\s>]|<input[\s>]/i.test(dialog), "the pop-up has a form or an input");
});

test("the script is loaded by the home page only, from the site's own address, deferred", () => {
  assert.equal([...home.matchAll(/<script\b[^>]*>/gi)].map((m) => m[0]).join(""), '<script src="/popup.js" defer>');
});

test("every hook popup.js looks for is in the markup, and the pop-up never reaches another site", () => {
  for (const hook of ['[data-offer="signup"]', '[data-offer="copy"]', '[data-offer="x"]', "[data-offer-status]"]) {
    assert.ok(code.includes(hook), `popup.js does not look for ${hook}`);
    const attribute = hook.replace(/^\[|\]$/g, "").replace(/"/g, '"');
    assert.ok(home.includes(attribute.includes("=") ? attribute : ` ${attribute}`), `the markup lacks ${attribute}`);
  }
  assert.ok(!/https?:/.test(code));
});

// ---- the behaviour ------------------------------------------------------------------------------------------------

function element(extra = {}) {
  const listeners = {};
  return { listeners, addEventListener: (type, fn) => (listeners[type] ??= []).push(fn), ...extra };
}

function page({ seen = false, beacon = true, storage = true, clipboard = "works", showModal = true } = {}) {
  const sent = [];
  const fetched = [];
  const timers = [];
  const kept = new Map(seen ? [["launch-offer-seen", "1"]] : []);
  const signup = element({ href: SIGNUP });
  const copy = element();
  const x = element();
  const status = { textContent: "" };
  const dialog = element({
    open: false,
    close() {
      this.open = false;
    },
    querySelector: (selector) =>
      ({ '[data-offer="signup"]': signup, '[data-offer="copy"]': copy, '[data-offer="x"]': x, "[data-offer-status]": status })[selector] ?? null,
  });
  if (showModal) {
    dialog.showModal = function () {
      this.open = true;
    };
  }
  const copied = [];
  const navigator = {
    sendBeacon: beacon === "throws" ? () => { throw new Error("no beacon"); } : beacon ? (url) => (sent.push(url), true) : undefined,
    clipboard:
      clipboard === "none"
        ? undefined
        : { writeText: (text) => (copied.push(text), clipboard === "works" ? Promise.resolve() : Promise.reject(new Error("refused"))) },
  };
  const sandbox = {
    document: { getElementById: (id) => (id === "offer" ? dialog : null) },
    window: {
      setTimeout: (fn, ms) => timers.push({ fn, ms }),
      get sessionStorage() {
        if (!storage) throw new Error("storage is blocked");
        return { getItem: (key) => kept.get(key) ?? null, setItem: (key, value) => kept.set(key, value) };
      },
    },
    navigator,
    fetch: (url, init) => (fetched.push({ url, init }), Promise.resolve()),
  };
  vm.runInNewContext(code, sandbox);
  const fire = (target, type, event = {}) => (target.listeners[type] ?? []).forEach((fn) => fn(event));
  return { dialog, signup, copy, x, status, sent, fetched, timers, kept, copied, fire, show: () => timers.forEach((t) => t.fn()) };
}

test("it waits 8 seconds, then shows once and counts one 'shown', and remembers that for this visit only", () => {
  const p = page();
  assert.deepEqual(p.timers.map((t) => t.ms), [8000]);
  assert.equal(p.dialog.open, false);
  assert.deepEqual(p.sent, []);
  p.show();
  assert.equal(p.dialog.open, true);
  assert.deepEqual(p.sent, ["/api/e/shown"]);
  assert.equal(p.kept.get("launch-offer-seen"), "1");
});

test("the X counts 'x' and closes it; a second way of closing it counts nothing more", () => {
  const p = page();
  p.show();
  p.fire(p.x, "click");
  assert.equal(p.dialog.open, false);
  p.fire(p.dialog, "cancel");
  p.fire(p.dialog, "click", { target: p.dialog });
  assert.deepEqual(p.sent, ["/api/e/shown", "/api/e/x"]);
});

test("a click on the dimmed area counts 'away' and closes it; a click on what is inside does not", () => {
  const p = page();
  p.show();
  p.fire(p.dialog, "click", { target: p.signup });
  assert.equal(p.dialog.open, true);
  assert.deepEqual(p.sent, ["/api/e/shown"]);
  p.fire(p.dialog, "click", { target: p.dialog });
  assert.equal(p.dialog.open, false);
  assert.deepEqual(p.sent, ["/api/e/shown", "/api/e/away"]);
});

test("Escape counts 'esc' (the browser closes the dialog itself)", () => {
  const p = page();
  p.show();
  p.fire(p.dialog, "cancel");
  assert.deepEqual(p.sent, ["/api/e/shown", "/api/e/esc"]);
});

test("Sign up now counts 'signup' and leaves the page to follow the link, which carries the code", () => {
  const p = page();
  p.show();
  p.fire(p.signup, "click");
  assert.deepEqual(p.sent, ["/api/e/shown", "/api/e/signup"]);
  assert.equal(p.dialog.open, true);
  assert.ok(p.signup.href.endsWith("?promo=LAUNCH10"));
  p.fire(p.dialog, "cancel"); // a dismissal after pressing sign-up is not counted as a way of leaving
  assert.deepEqual(p.sent, ["/api/e/shown", "/api/e/signup"]);
});

test("Copy link counts 'copy', copies the sign-up link with the code, and says so", async () => {
  const p = page();
  p.show();
  p.fire(p.copy, "click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(p.sent, ["/api/e/shown", "/api/e/copy"]);
  assert.deepEqual(p.copied, [SIGNUP]);
  assert.match(p.status.textContent, /^Link copied\./);
  assert.equal(p.dialog.open, true);
});

test("when copying is refused or the browser has no clipboard, it says what the code is and breaks nothing", async () => {
  for (const clipboard of ["refused", "none"]) {
    const p = page({ clipboard });
    p.show();
    p.fire(p.copy, "click");
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(p.sent, ["/api/e/shown", "/api/e/copy"], clipboard);
    assert.equal(p.status.textContent, "Could not copy. The code is LAUNCH10.", clipboard);
  }
});

test("a visitor who has seen it this visit is not shown it again, and nothing is counted", () => {
  const p = page({ seen: true });
  p.show();
  assert.equal(p.dialog.open, false);
  assert.deepEqual(p.sent, []);
});

test("with storage blocked it still works (it may show again on the next page load)", () => {
  const p = page({ storage: false });
  p.show();
  assert.equal(p.dialog.open, true);
  assert.deepEqual(p.sent, ["/api/e/shown"]);
});

test("without sendBeacon, or when it throws, the event goes by fetch: a POST that survives leaving the page, with no credentials", () => {
  for (const beacon of [false, "throws"]) {
    const p = page({ beacon });
    p.show();
    assert.deepEqual(p.sent, []);
    assert.equal(p.fetched.length, 1);
    assert.equal(p.fetched[0].url, "/api/e/shown");
    assert.deepEqual(JSON.parse(JSON.stringify(p.fetched[0].init)), { method: "POST", keepalive: true, credentials: "omit" }); // across the vm's realm
  }
});

test("a browser without <dialog> support gets a page with no pop-up and no error", () => {
  const p = page({ showModal: false });
  assert.deepEqual(p.timers, []);
  assert.deepEqual(p.sent, []);
});
