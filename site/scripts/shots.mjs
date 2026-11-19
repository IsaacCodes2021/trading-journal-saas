// Makes the marked-up screenshots of the docs pages (public/docs/) from raw screenshots kept OUTSIDE the repository.
//
//   cd site
//   node scripts/shots.mjs --from ../../guide-screenshots
//
// For each entry of shots.json it crops the raw screenshot, paints over the private parts (account numbers, names) in
// the pixels themselves, writes the PNG under public/, and rewrites the image and its marks in the page, between
// <!-- shot:<id> --> and <!-- /shot:<id> -->. Only the cropped, painted PNG reaches the repository; a test fails if an
// image under public/docs/ is not one of these outputs, or if a page's marks no longer match shots.json.
//
// shots.json, one entry per image:
//   id       the marker's name in the page
//   src      the raw screenshot, relative to --from (an 8-bit RGB or RGBA PNG, as Windows and browsers save them)
//   scale    image pixels per CSS pixel in the raw screenshot: 2 for a capture at twice the size, 1 otherwise
//   crop     [x, y, width, height] of the part shown, in CSS pixels of the raw screenshot
//   redact   rectangles [x, y, width, height, "#rrggbb"] painted over before cropping, same units
//   out      where the PNG goes, under public/
//   alt      the image's text alternative
//   marks    boxes drawn on top: { n, box: [x, y, width, height], pin: "tr" | "tl" | "br" | "bl" | "l" | "r", tone: "no" }
//            n is the number in the circle ("×" for tone "no"); the pin sits on the box's corner (top right by default),
//            or halfway down its left or right side
//
// No dependencies: PNG is read and written with node:zlib. The marks are an <svg> laid over the image, so they stay
// sharp, their numbers stay the same size at any width, and the page needs no inline style (the site's CSP forbids it).
import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync, inflateSync } from "node:zlib";

const SITE = fileURLToPath(new URL("../", import.meta.url));
export const PUBLIC = join(SITE, "public");
export const SHOTS = JSON.parse(readFileSync(join(SITE, "shots.json"), "utf8")).shots;

// ---- PNG ----

const SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (buf) => {
  let c = 0xffffffff;
  for (const byte of buf) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};

/** Width and height from a PNG's header, without decoding it. */
export function pngSize(buf) {
  if (!buf.subarray(0, 8).equals(SIGNATURE)) throw new Error("not a PNG");
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

/** Decodes an 8-bit, non-interlaced RGB or RGBA PNG into RGBA pixels. */
export function readPng(buf) {
  const { width, height } = pngSize(buf);
  const [depth, type, interlace] = [buf[24], buf[25], buf[28]];
  if (depth !== 8 || (type !== 2 && type !== 6) || interlace !== 0) {
    throw new Error(`only 8-bit RGB or RGBA PNGs without interlacing (this one: depth ${depth}, colour type ${type}, interlace ${interlace})`);
  }
  const parts = [];
  for (let off = 8; off < buf.length; ) {
    const len = buf.readUInt32BE(off);
    const name = buf.toString("latin1", off + 4, off + 8);
    if (name === "IDAT") parts.push(buf.subarray(off + 8, off + 8 + len));
    if (name === "IEND") break;
    off += 12 + len;
  }
  const bpp = type === 6 ? 4 : 3;
  const stride = width * bpp;
  const raw = inflateSync(Buffer.concat(parts));
  const data = Buffer.alloc(width * height * 4);
  let prev = Buffer.alloc(stride);
  let cur = Buffer.alloc(stride);
  for (let y = 0; y < height; y++) {
    const base = y * (stride + 1);
    const filter = raw[base];
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? cur[i - bpp] : 0;
      const b = prev[i];
      const c = i >= bpp ? prev[i - bpp] : 0;
      let v = raw[base + 1 + i];
      if (filter === 1) v += a;
      else if (filter === 2) v += b;
      else if (filter === 3) v += (a + b) >> 1;
      else if (filter === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a);
        const pb = Math.abs(p - b);
        const pc = Math.abs(p - c);
        v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      } else if (filter !== 0) throw new Error(`bad PNG filter ${filter} on row ${y}`);
      cur[i] = v & 0xff;
    }
    for (let x = 0; x < width; x++) {
      const o = (y * width + x) * 4;
      data[o] = cur[x * bpp];
      data[o + 1] = cur[x * bpp + 1];
      data[o + 2] = cur[x * bpp + 2];
      data[o + 3] = bpp === 4 ? cur[x * bpp + 3] : 255;
    }
    [prev, cur] = [cur, prev];
  }
  return { width, height, data };
}

/** Encodes RGBA pixels as a PNG (RGB when every pixel is opaque), choosing each row's filter for size. */
export function writePng({ width, height, data }) {
  let opaque = true;
  for (let i = 3; i < data.length; i += 4) if (data[i] !== 255) { opaque = false; break; }
  const bpp = opaque ? 3 : 4;
  const stride = width * bpp;
  const out = Buffer.alloc(height * (stride + 1));
  let prev = Buffer.alloc(stride);
  let line = Buffer.alloc(stride);
  const tries = Array.from({ length: 5 }, () => Buffer.alloc(stride));
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      for (let k = 0; k < bpp; k++) line[x * bpp + k] = data[(y * width + x) * 4 + k];
    }
    let best = 0;
    let bestScore = Infinity;
    for (let f = 0; f < 5; f++) {
      const t = tries[f];
      let score = 0;
      for (let i = 0; i < stride; i++) {
        const a = i >= bpp ? line[i - bpp] : 0;
        const b = prev[i];
        const c = i >= bpp ? prev[i - bpp] : 0;
        let pred = 0;
        if (f === 1) pred = a;
        else if (f === 2) pred = b;
        else if (f === 3) pred = (a + b) >> 1;
        else if (f === 4) {
          const p = a + b - c;
          const pa = Math.abs(p - a);
          const pb = Math.abs(p - b);
          const pc = Math.abs(p - c);
          pred = pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
        }
        const v = (line[i] - pred) & 0xff;
        t[i] = v;
        score += v < 128 ? v : 256 - v;
      }
      if (score < bestScore) [best, bestScore] = [f, score];
    }
    out[y * (stride + 1)] = best;
    tries[best].copy(out, y * (stride + 1) + 1);
    [prev, line] = [line, prev];
  }
  const chunk = (name, body) => {
    const head = Buffer.alloc(4);
    head.writeUInt32BE(body.length);
    const typed = Buffer.concat([Buffer.from(name, "latin1"), body]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(typed));
    return Buffer.concat([head, typed, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = opaque ? 2 : 6;
  return Buffer.concat([
    SIGNATURE,
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(out, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function paint(image, [x, y, w, h], hex) {
  const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  for (let row = Math.max(0, y); row < Math.min(image.height, y + h); row++) {
    for (let col = Math.max(0, x); col < Math.min(image.width, x + w); col++) {
      const o = (row * image.width + col) * 4;
      [image.data[o], image.data[o + 1], image.data[o + 2], image.data[o + 3]] = [...rgb, 255];
    }
  }
}

function crop(image, [x, y, w, h]) {
  if (x < 0 || y < 0 || x + w > image.width || y + h > image.height) throw new Error(`crop ${[x, y, w, h]} is outside the ${image.width}×${image.height} image`);
  const data = Buffer.alloc(w * h * 4);
  for (let row = 0; row < h; row++) image.data.copy(data, row * w * 4, ((y + row) * image.width + x) * 4, ((y + row) * image.width + x + w) * 4);
  return { width: w, height: h, data };
}

// ---- the markup ----

const pct = (value) => `${+(value * 100).toFixed(2)}%`;
const esc = (text) => text.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");

/** The image and its marks, as they appear between the shot's markers (one line per element, no indentation). */
export function renderShot(shot) {
  const [cx, cy, cw, ch] = shot.crop;
  const lines = [
    `<a class="shot-frame" href="/${shot.out}" title="Open the full-size image">`,
    `<img src="/${shot.out}" width="${cw}" height="${ch}" alt="${esc(shot.alt)}" loading="lazy" decoding="async">`,
  ];
  if (shot.marks?.length) {
    lines.push('<svg class="shot-marks" aria-hidden="true" focusable="false">');
    for (const mark of shot.marks) {
      const [x, y, w, h] = mark.box;
      const left = (x - cx) / cw;
      const top = (y - cy) / ch;
      const right = (x + w - cx) / cw;
      const bottom = (y + h - cy) / ch;
      const corner = mark.pin ?? "tr";
      const px = corner.endsWith("l") ? left : right;
      const py = corner.length === 1 ? (top + bottom) / 2 : corner.startsWith("b") ? bottom : top;
      const tone = mark.tone ? ` ${mark.tone}` : "";
      const label = mark.tone === "no" ? "×" : String(mark.n);
      lines.push(`<rect class="shot-box${tone}" x="${pct(left)}" y="${pct(top)}" width="${pct(right - left)}" height="${pct(bottom - top)}" rx="6"/>`);
      lines.push(`<g class="shot-pin${tone}"><circle cx="${pct(px)}" cy="${pct(py)}" r="13"/><text x="${pct(px)}" y="${pct(py)}">${label}</text></g>`);
    }
    lines.push("</svg>");
  }
  lines.push("</a>");
  return lines;
}

const MARKER = (id) => new RegExp(`([ \\t]*)<!-- shot:${id} -->([\\s\\S]*?)<!-- /shot:${id} -->`);

/** The pages under public/, as [path, text]. */
export function pages() {
  const walk = (dir) => readdirSync(dir).flatMap((name) => (statSync(join(dir, name)).isDirectory() ? walk(join(dir, name)) : [join(dir, name)]));
  return walk(PUBLIC).filter((f) => f.endsWith(".html")).map((f) => [f, readFileSync(f, "utf8")]);
}

/** The text between a shot's markers, rebuilt from shots.json with the page's own indentation and line endings. */
export function blockFor(shot, indent, eol) {
  return [`${indent}<!-- shot:${shot.id} -->`, ...renderShot(shot).map((l) => indent + l), `${indent}<!-- /shot:${shot.id} -->`].join(eol);
}

// ---- run ----

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const at = process.argv.indexOf("--from");
  if (at < 0 || !process.argv[at + 1]) {
    console.error("Usage: node scripts/shots.mjs --from <folder of raw screenshots> [id ...]");
    process.exit(2);
  }
  const from = resolve(process.argv[at + 1]);
  const only = process.argv.slice(2).filter((a, i, all) => a !== "--from" && all[i - 1] !== "--from");
  const texts = new Map(pages());
  for (const shot of SHOTS) {
    if (only.length && !only.includes(shot.id)) continue;
    const scale = shot.scale ?? 1;
    const px = (r) => r.slice(0, 4).map((v) => Math.round(v * scale));
    const image = readPng(readFileSync(join(from, shot.src)));
    for (const r of shot.redact ?? []) paint(image, px(r), r[4]);
    const png = writePng(crop(image, px(shot.crop)));
    mkdirSync(dirname(join(PUBLIC, shot.out)), { recursive: true });
    writeFileSync(join(PUBLIC, shot.out), png);
    let placed = 0;
    for (const [file, text] of texts) {
      const match = MARKER(shot.id).exec(text);
      if (!match) continue;
      const eol = text.includes("\r\n") ? "\r\n" : "\n";
      const updated = text.replace(MARKER(shot.id), blockFor(shot, match[1], eol));
      texts.set(file, updated);
      writeFileSync(file, updated);
      placed++;
    }
    console.log(`${shot.id}: ${shot.out}, ${Math.round(png.length / 1024)} KB${placed ? "" : " (no page has its marker yet)"}`);
  }
}
