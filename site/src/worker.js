// The Worker in front of the static site. Cloudflare serves everything under public/ itself and runs this code only for
// /api/* (see run_worker_first in wrangler.jsonc), so a page view never costs a Worker invocation.
//
// The site has no API: the sign-up and sign-in forms live in the app (app.roundturn.app), which is the only place that
// stores anything about people. So every /api path is a plain 404, with one exception: the launch offer pop-up on the
// home page (public/popup.js) reports what a visitor did with it, as the name of the event and nothing else, to
// POST /api/e/<name>. Each report becomes one tally in a Workers Analytics Engine dataset (see wrangler.jsonc). The
// request is never read: no IP address, browser details, referrer, cookie or body is looked at or kept.
const SAFE_HEADERS = { "cache-control": "no-store", "x-content-type-options": "nosniff" };

// What the pop-up can report: it was shown, then at most one of closed with the X, clicked away from, or closed with
// Escape; and whether the link was copied or the sign-up button pressed.
export const POPUP_EVENTS = ["shown", "x", "away", "esc", "copy", "signup"];

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname.startsWith("/api/")) {
      const event = request.method === "POST" ? /^\/api\/e\/([a-z]+)$/.exec(pathname)?.[1] : undefined;
      if (event !== undefined && POPUP_EVENTS.includes(event)) {
        // `env.POPUP_EVENTS` is missing when the Worker runs without the binding (a test, a local run): then it is a no-op.
        env.POPUP_EVENTS?.writeDataPoint({ indexes: [event], blobs: [event], doubles: [1] });
        return new Response(null, { status: 204, headers: SAFE_HEADERS });
      }
      return new Response("Not found.", { status: 404, headers: SAFE_HEADERS });
    }
    return env.ASSETS.fetch(request); // not expected to reach here, but a static file is the right answer if it does
  },
};
