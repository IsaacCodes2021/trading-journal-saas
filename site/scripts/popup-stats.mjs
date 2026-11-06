// What people did with the launch offer pop-up: the tallies the Worker keeps (src/worker.js), read back from Cloudflare.
//
//   node scripts/popup-stats.mjs [--days 30] [--env-file ../.env.cloudflare]
//
// It needs two values, kept out of the chat and out of git (any `.env.*` file is ignored by git):
//   CLOUDFLARE_ACCOUNT_ID  your account id (the dashboard shows it on the Workers overview)
//   CLOUDFLARE_API_TOKEN   an API token with the permission "Account Analytics: Read" and nothing else
// They can be in the environment, or in a KEY=VALUE file named with --env-file. The token is only ever sent to Cloudflare.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const DATASET = "roundturn_popup";
const DISMISSALS = ["x", "away", "esc"];
const PROCEEDS = ["copy", "signup"];

/** The question put to Cloudflare's SQL API: one row per kind of event, with how many there were in the last `days`. */
export function query(days) {
  if (!/^\d{1,3}$/.test(String(days))) throw new Error("--days must be a whole number from 1 to 365");
  const n = Number(days);
  if (n < 1 || n > 365) throw new Error("--days must be a whole number from 1 to 365");
  return `SELECT index1 AS event, SUM(_sample_interval * double1) AS n FROM ${DATASET} WHERE timestamp > NOW() - INTERVAL '${n}' DAY GROUP BY event`;
}

const percent = (part, whole) => (whole > 0 ? `${((part / whole) * 100).toFixed(1)}%` : "n/a");

/** Plain text for the owner: the tallies, and how the people who saw the pop-up split between leaving and going on. */
export function report(rows, days) {
  const count = Object.fromEntries(rows.map((row) => [row.event, Math.round(Number(row.n))]));
  const get = (name) => count[name] ?? 0;
  const shown = get("shown");
  const left = DISMISSALS.reduce((sum, name) => sum + get(name), 0);
  const went = PROCEEDS.reduce((sum, name) => sum + get(name), 0);
  const lines = [
    `Launch offer pop-up, last ${days} days`,
    "",
    `Shown                         ${shown}`,
    `  Closed with the X           ${get("x")}`,
    `  Clicked away                ${get("away")}`,
    `  Closed with Escape          ${get("esc")}`,
    `  Pressed Copy link           ${get("copy")}`,
    `  Pressed Sign up now         ${get("signup")}`,
    "",
    `Left without acting           ${left}  (${percent(left, shown)} of those shown)`,
    `Went on (copied or signed up) ${went}  (${percent(went, shown)} of those shown)`,
    "",
    "Copy and sign-up can both happen in one visit, so the two groups can add to more than the number shown.",
  ];
  const unknown = Object.keys(count).filter((name) => ![ "shown", ...DISMISSALS, ...PROCEEDS ].includes(name));
  if (unknown.length > 0) lines.push(`Other events found: ${unknown.join(", ")}`);
  return lines.join("\n");
}

function readEnvFile(path) {
  const values = {};
  for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
    if (line.includes("=") && !line.trimStart().startsWith("#")) {
      const at = line.indexOf("=");
      values[line.slice(0, at).trim()] = line.slice(at + 1).trim();
    }
  }
  return values;
}

export async function main(argv, env = process.env, fetchImpl = fetch, out = console.log) {
  const args = { days: "30", envFile: undefined };
  for (let i = 0; i < argv.length; i += 2) {
    if (argv[i] === "--days") args.days = argv[i + 1];
    else if (argv[i] === "--env-file") args.envFile = argv[i + 1];
    else throw new Error(`unknown option ${argv[i]}`);
  }
  const settings = { ...env, ...(args.envFile ? readEnvFile(args.envFile) : {}) };
  const account = settings.CLOUDFLARE_ACCOUNT_ID?.trim();
  const token = settings.CLOUDFLARE_API_TOKEN?.trim();
  if (!account || !token) {
    throw new Error("set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN (in the environment or with --env-file)");
  }
  const response = await fetchImpl(`https://api.cloudflare.com/client/v4/accounts/${account}/analytics_engine/sql`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "text/plain" },
    body: query(args.days),
  });
  if (!response.ok) throw new Error(`Cloudflare answered ${response.status}; check the account id and that the token can read analytics`);
  const body = await response.json();
  out(report(body.data ?? [], args.days));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((problem) => {
    console.error(`popup-stats: ${problem.message}`); // never the token: it is not part of any message above
    process.exitCode = 1;
  });
}
