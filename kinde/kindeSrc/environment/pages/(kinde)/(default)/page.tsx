"use server";
import React from "react";
import { renderToString } from "react-dom/server.browser";
import {
  getKindeCSRF,
  getKindeNonce,
  getKindeRequiredCSS,
  getKindeRequiredJS,
  getKindeWidget,
  setKindeDesignerCustomProperties,
  type KindePageEvent,
} from "@kinde/infrastructure";

/*
 * Roundturn's look for Kinde's hosted sign-in screens (the email, one-time code, password and second-factor steps).
 * One page for every screen: Kinde fills in the widget, and this wraps it in the same name, card and colours as the
 * app's own sign-in and sign-up pages (frontend/src/features/auth). The colours are the app's tokens
 * (frontend/src/styles/tokens.css); the font files are the ones the marketing site already serves.
 *
 * Not run anywhere yet: it needs Kinde's custom domain and a repository connection (docs/runbooks/kinde-branding.md).
 * Check it in a non-production environment first.
 */

const FONTS = "https://roundturn.app/fonts"; // self-hosted on the marketing site; falls back to the system font if blocked

const CSS = `
@font-face { font-family: "IBM Plex Sans"; font-weight: 400; font-display: swap; src: url("${FONTS}/ibm-plex-sans-latin-400-normal.woff2") format("woff2"); }
@font-face { font-family: "IBM Plex Sans"; font-weight: 500; font-display: swap; src: url("${FONTS}/ibm-plex-sans-latin-500-normal.woff2") format("woff2"); }
@font-face { font-family: "IBM Plex Sans"; font-weight: 600; font-display: swap; src: url("${FONTS}/ibm-plex-sans-latin-600-normal.woff2") format("woff2"); }

:root {
  --rt-bg: #0d1117;
  --rt-panel: #141a22;
  --rt-line: #263040;
  --rt-text: #e8edf3;
  --rt-muted: #93a0b2;
  --rt-accent: #6ea8fe;
  --rt-pos: #34d399;
  --kinde-base-font-family: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --kinde-base-letter-spacing: 0;
  --kinde-base-background-color: var(--rt-bg);
  --kinde-base-background-color-dark: var(--rt-bg);
  --kinde-base-color: var(--rt-text);
  --kinde-base-color-dark: var(--rt-text);
  --kinde-base-accent-color: var(--rt-accent);
  --kinde-base-accent-color-dark: var(--rt-accent);
  --kinde-base-focus-outline-color: var(--rt-accent);
  --kinde-base-focus-outline-color-dark: var(--rt-accent);
}

@media (prefers-color-scheme: light) {
  :root {
    --rt-bg: #f4f6fa;
    --rt-panel: #ffffff;
    --rt-line: #d3dae5;
    --rt-text: #17202b;
    --rt-muted: #566476;
    --rt-accent: #1f5fd1;
    --rt-pos: #0b7d55;
  }
}

body.rt-page {
  margin: 0;
  min-height: 100vh;
  background: var(--rt-bg);
  color: var(--rt-text);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 56px 24px 32px;
  box-sizing: border-box;
}

.rt-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 19px;
  font-weight: 600;
}

/* The bite mark, as in the app (frontend/src/components/BiteMark.tsx and styles.css): two bites, then still. */
.rt-mark {
  flex: none;
  width: 30px;
  height: 30px;
}

.rt-mark .rt-ring {
  fill: none;
  stroke: var(--rt-accent);
  stroke-width: 11;
  stroke-dasharray: 111.7 13.96;
}

.rt-mark .rt-head {
  fill: var(--rt-accent);
  transform-origin: 32px 32px;
}

.rt-mark .rt-dot {
  fill: var(--rt-pos);
  transform-origin: 32px 32px;
}

@media (prefers-reduced-motion: no-preference) {
  .rt-mark .rt-ring {
    animation: rt-bite-ring 2s cubic-bezier(0.6, 0, 0.2, 1) 2;
  }

  .rt-mark .rt-head {
    animation: rt-bite-head 2s cubic-bezier(0.6, 0, 0.2, 1) 2;
  }

  .rt-mark .rt-dot {
    animation: rt-bite-dot 2s cubic-bezier(0.3, 1.6, 0.5, 1) 2;
  }
}

@keyframes rt-bite-ring {
  0%, 18%, 82%, 100% { stroke-dasharray: 111.7 13.96; }
  44%, 56% { stroke-dasharray: 114.84 10.82; }
}

@keyframes rt-bite-head {
  0%, 18%, 82%, 100% { transform: rotate(0deg); }
  44%, 56% { transform: rotate(9deg); }
}

@keyframes rt-bite-dot {
  0%, 42%, 68%, 100% { transform: scale(1); }
  52% { transform: scale(1.55); }
}

.rt-card {
  margin-top: 36px;
  width: 100%;
  max-width: 440px;
  box-sizing: border-box;
  border: 1px solid var(--rt-line);
  border-radius: 16px;
  background: var(--rt-panel);
  padding: 32px;
}

.rt-card h1 {
  margin: 0;
  font-size: 28px;
  line-height: 1.2;
  letter-spacing: -0.02em;
  font-weight: 600;
}

.rt-card p.rt-lead {
  margin: 8px 0 24px;
  font-size: 15px;
  color: var(--rt-muted);
}

.rt-foot {
  margin-top: 24px;
  max-width: 440px;
  text-align: center;
  font-size: 12px;
  color: var(--rt-muted);
}

.rt-foot a {
  color: var(--rt-accent);
  text-decoration: none;
}
`;

const Page = async ({ request, context }: KindePageEvent) => {
  // The documented design properties; everything else is in the stylesheet above.
  setKindeDesignerCustomProperties({
    baseBackgroundColor: "#0d1117",
    baseLinkColor: "#6ea8fe",
    buttonBorderRadius: "12px",
    primaryButtonBackgroundColor: "#6ea8fe",
    primaryButtonColor: "#07121f",
    inputBorderRadius: "10px",
  });

  return (
    <html lang={request.locale.lang} dir={request.locale.isRtl ? "rtl" : "ltr"}>
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="csrf-token" content={getKindeCSRF()} />
        <title>{context.widget.content.page_title}</title>
        {getKindeRequiredCSS()}
        {getKindeRequiredJS()}
        <style nonce={getKindeNonce()} dangerouslySetInnerHTML={{ __html: CSS }} />
      </head>
      <body className="rt-page">
        <div className="rt-brand">
          <svg className="rt-mark" viewBox="0 0 64 64" aria-hidden="true">
            <circle className="rt-ring" cx="32" cy="32" r="20" transform="rotate(-60 32 32)" />
            <polygon className="rt-head" points="39.36,10.39 30.26,22.15 26.79,2.46" />
            <circle className="rt-dot" cx="32" cy="32" r="6" />
          </svg>
          Roundturn
        </div>
        <main className="rt-card">
          <h1>{context.widget.content.heading}</h1>
          {context.widget.content.description ? (
            <p className="rt-lead">{context.widget.content.description}</p>
          ) : null}
          <div>{getKindeWidget()}</div>
        </main>
        <p className="rt-foot">
          <a href="https://app.roundturn.app/signin">Back to Roundturn</a>
        </p>
      </body>
    </html>
  );
};

export default async function handler(event: KindePageEvent): Promise<string> {
  const page = await Page(event);
  return renderToString(page);
}
