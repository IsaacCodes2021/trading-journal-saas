// POST /api/waitlist: a Cloudflare Pages Function that stores one email address and the date it joined.
//
// Needs a D1 database bound as `DB` (see site/README.md). It stores nothing else: no IP address, no user agent, no
// referrer. It never logs the address, and it answers the same way whether or not the address was already on the list,
// so the form cannot be used to find out who has signed up.

const MAX_BODY_BYTES = 2048;
const EMAIL = /^[^\s@,;<>()"'\\]+@[^\s@,;<>()"'\\]+\.[^\s@,;<>()"'\\]{2,}$/;
const SCHEMA = "CREATE TABLE IF NOT EXISTS waitlist (email TEXT PRIMARY KEY, created_at TEXT NOT NULL)";

// One schema check per database binding per isolate, so a request normally costs one statement.
const ready = new WeakMap();

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export function normalizeEmail(value) {
  if (typeof value !== "string") return null;
  const email = value.trim().toLowerCase();
  return email.length >= 6 && email.length <= 254 && EMAIL.test(email) ? email : null;
}

async function readBody(request) {
  if (Number(request.headers.get("content-length") ?? 0) > MAX_BODY_BYTES) throw new HttpError(413, "Request too large.");
  const text = await request.text();
  if (text.length > MAX_BODY_BYTES) throw new HttpError(413, "Request too large.");
  const type = request.headers.get("content-type") ?? "";
  if (type.includes("application/json")) {
    try {
      const body = JSON.parse(text);
      if (body === null || typeof body !== "object" || Array.isArray(body)) throw new Error("not an object");
      return body;
    } catch {
      throw new HttpError(400, "That request could not be read.");
    }
  }
  if (type.includes("application/x-www-form-urlencoded")) return Object.fromEntries(new URLSearchParams(text));
  throw new HttpError(415, "Unsupported content type.");
}

function wantsJson(request) {
  return (request.headers.get("content-type") ?? "").includes("application/json") || (request.headers.get("accept") ?? "").includes("application/json");
}

const SAFE_HEADERS = { "cache-control": "no-store", "x-content-type-options": "nosniff" };

function answer(request, status, { ok, error }) {
  if (wantsJson(request)) {
    return Response.json(ok ? { ok: true } : { ok: false, error }, { status, headers: SAFE_HEADERS });
  }
  if (ok) return new Response(null, { status: 303, headers: { ...SAFE_HEADERS, location: "/thanks/" } });
  const page = `<!doctype html><meta charset="utf-8"><title>Roundturn</title><p>${error} <a href="/">Go back</a>.</p>`;
  return new Response(page, { status, headers: { ...SAFE_HEADERS, "content-type": "text/html; charset=utf-8" } });
}

async function ensureSchema(db) {
  if (!ready.has(db)) {
    ready.set(
      db,
      db
        .prepare(SCHEMA)
        .run()
        .catch((error) => {
          ready.delete(db); // try again on the next request
          throw error;
        }),
    );
  }
  await ready.get(db);
}

export async function onRequestPost({ request, env }) {
  try {
    // Reject another site's form posting here. Browsers always send Origin on a cross-site POST.
    const origin = request.headers.get("origin");
    if ((origin && origin !== new URL(request.url).origin) || request.headers.get("sec-fetch-site") === "cross-site") {
      throw new HttpError(403, "That request was not allowed.");
    }
    const body = await readBody(request);

    // The honeypot field is invisible to people. A bot that fills it gets a success answer and nothing is stored.
    if (typeof body.website === "string" && body.website !== "") return answer(request, 200, { ok: true });

    const email = normalizeEmail(body.email);
    if (!email) throw new HttpError(400, "Please enter a valid email address.");

    await ensureSchema(env.DB);
    await env.DB.prepare("INSERT OR IGNORE INTO waitlist (email, created_at) VALUES (?, ?)")
      .bind(email, new Date().toISOString().slice(0, 10))
      .run();
    return answer(request, 200, { ok: true });
  } catch (error) {
    if (error instanceof HttpError) return answer(request, error.status, { ok: false, error: error.message });
    // Nothing about the request is logged: it holds an email address.
    return answer(request, 500, { ok: false, error: "Something went wrong. Please try again." });
  }
}

export function onRequest() {
  return new Response("Method not allowed.", { status: 405, headers: { ...SAFE_HEADERS, allow: "POST" } });
}
