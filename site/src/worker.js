// The Worker in front of the static site. Cloudflare serves everything under public/ itself and runs this code only for
// /api/* (see run_worker_first in wrangler.jsonc), so a page view never costs a Worker invocation.
import { handleWaitlist, methodNotAllowed } from "./waitlist.js";

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/api/waitlist") {
      return request.method === "POST" ? handleWaitlist({ request, env }) : methodNotAllowed();
    }
    if (pathname.startsWith("/api/")) {
      return new Response("Not found.", { status: 404, headers: { "cache-control": "no-store", "x-content-type-options": "nosniff" } });
    }
    return env.ASSETS.fetch(request); // not expected to reach here, but a static file is the right answer if it does
  },
};
