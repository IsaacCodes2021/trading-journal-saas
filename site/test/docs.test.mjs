// Checks on the docs pages (public/docs/): every export guide the app links to exists, every image there is one that
// scripts/shots.mjs made (cropped, with private parts painted over), and each page's marks are the ones in shots.json.
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { PUBLIC, SHOTS, blockFor, pages, pngSize } from "../scripts/shots.mjs";

const DOCS = join(PUBLIC, "docs");
const urlOf = (file) => "/" + relative(PUBLIC, file).split(sep).join("/");
const walk = (dir) => readdirSync(dir).flatMap((name) => (statSync(join(dir, name)).isDirectory() ? walk(join(dir, name)) : [join(dir, name)]));
const docPages = pages().filter(([file]) => file.startsWith(DOCS));

test("every export guide the app links to is a page here", () => {
  const platforms = readFileSync(fileURLToPath(new URL("../../frontend/src/lib/platforms.ts", import.meta.url)), "utf8");
  const base = platforms.match(/DOCS_IMPORT = '(https:\/\/roundturn\.app(\/docs\/import\/))'/);
  assert.ok(base, "platforms.ts no longer sets DOCS_IMPORT to https://roundturn.app/docs/import/");
  const guides = [...platforms.matchAll(/guide: `\$\{DOCS_IMPORT\}([a-z0-9-]+\/)`/g)].map((m) => base[2] + m[1]);
  assert.ok(guides.length >= 3, `found only ${guides.length} guides in platforms.ts`);
  for (const path of [base[2], ...guides]) {
    assert.ok(existsSync(join(PUBLIC, path, "index.html")), `the app links to https://roundturn.app${path}, which has no page`);
  }
});

test("every docs page shows the docs menu, with itself as the current page", () => {
  assert.ok(docPages.length >= 5, `found only ${docPages.length} docs pages`);
  for (const [file, html] of docPages) {
    const url = urlOf(file).replace(/index\.html$/, "");
    const menu = html.match(/<nav class="docs-nav" aria-label="Docs">([\s\S]*?)<\/nav>/);
    assert.ok(menu, `${url} has no docs menu`);
    assert.match(menu[1], new RegExp(`<a href="${url}" aria-current="page">`), `${url} is not marked current in its menu`);
    assert.equal((menu[1].match(/aria-current/g) ?? []).length, 1, `${url} marks more than one page current`);
  }
});

test("every image under /docs/ is one that scripts/shots.mjs made, with its size in the page", () => {
  const made = new Set(SHOTS.map((s) => s.out));
  for (const file of walk(DOCS).filter((f) => !f.endsWith(".html"))) {
    const path = urlOf(file).slice(1);
    assert.ok(made.has(path), `${path} was not made by scripts/shots.mjs (so nothing says it was cropped and painted over)`);
  }
  for (const shot of SHOTS) {
    const file = join(PUBLIC, shot.out);
    assert.ok(existsSync(file), `${shot.out} is missing: run node scripts/shots.mjs`);
    const { width, height } = pngSize(readFileSync(file));
    const scale = shot.scale ?? 1;
    assert.deepEqual([width, height], [Math.round(shot.crop[2] * scale), Math.round(shot.crop[3] * scale)], `${shot.out} does not match its crop in shots.json`);
  }
});

test("each page's screenshots and marks are exactly what shots.json says", () => {
  for (const shot of SHOTS) {
    const found = docPages.filter(([, html]) => html.includes(`<!-- shot:${shot.id} -->`));
    assert.equal(found.length, 1, `shot ${shot.id} is on ${found.length} pages, not 1`);
    const [[file, html]] = found;
    const match = html.match(new RegExp(`([ \\t]*)<!-- shot:${shot.id} -->[\\s\\S]*?<!-- /shot:${shot.id} -->`));
    const eol = html.includes("\r\n") ? "\r\n" : "\n";
    assert.equal(match[0], blockFor(shot, match[1], eol), `${urlOf(file)}: shot ${shot.id} is out of date; run node scripts/shots.mjs`);
  }
});

test("a marked-up screenshot is an image with a text alternative, and its marks are hidden from screen readers", () => {
  for (const [file, html] of docPages) {
    for (const [img] of html.matchAll(/<img\b[^>]*>/g)) {
      assert.match(img, /\salt="[^"]{20,}"/, `${urlOf(file)}: an image has no useful alt text`);
      assert.match(img, /\swidth="\d+" height="\d+"/, `${urlOf(file)}: an image has no size, so the page jumps as it loads`);
    }
    for (const [svg] of html.matchAll(/<svg class="shot-marks"[^>]*>/g)) assert.match(svg, /aria-hidden="true"/);
  }
});
