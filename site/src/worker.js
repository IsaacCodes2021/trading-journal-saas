// The Worker in front of the static site. Cloudflare serves everything under public/ itself and runs this code only for
// /api/* (see run_worker_first in wrangler.jsonc), so a page view never costs a Worker invocation.
//
// The site has no API: the beta form lives in the app (app.roundturn.app), which is the only place that stores anything.
// So every /api path is a plain 404, and the site collects nothing of its own.
const SAFE_HEADERS = { "cache-control": "no-store", "x-content-type-options": "nosniff" };

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname.startsWith("/api/")) {
      return new Response("Not found.", { status: 404, headers: SAFE_HEADERS });
    }
    return env.ASSETS.fetch(request); // not expected to reach here, but a static file is the right answer if it does
  },
};
