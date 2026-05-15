#!/usr/bin/env node
/**
 * Blocklist scan for public site content before deploy / build.
 * Fails fast if internal paths, tailnet-style IPs, or obvious secret markers appear.
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const siteRoot = path.join(__dirname, "..");

const SKIP_DIRS = new Set(["node_modules", ".next", ".git"]);

const BLOCKS = [
  { name: "absolute srv path", re: /\/srv\//i },
  { name: "absolute root home path", re: /\/root\//i },
  { name: "tailscale keyword", re: /tailscale/i },
  { name: "openssh marker", re: /OPENSSH/i },
  { name: "ssh client/server mention", re: /\bssh\b/i },
  { name: "CGNAT / tailnet-style IPv4", re: /\b100\.(?:6[4-9]|[7-9]\d|1\d{2}|2[0-7]\d|28[0-7])\.\d{1,3}\.\d{1,3}\b/ },
  { name: "private LAN IPv4", re: /\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.(?:\d{1,3}\.){1}\d{1,3})\b/ },
  { name: "openclaw path", re: /\.openclaw/i },
  { name: "api.env filename", re: /api\.env/i },
  { name: "log view token env", re: /TRADE_LOG_VIEW_TOKEN/i },
];

const EXT = /\.(tsx?|md|mmd|json|css)$/i;
const SELF = path.resolve(fileURLToPath(import.meta.url));

function walk(dir, out = []) {
  for (const name of fs.readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const p = path.join(dir, name);
    const st = fs.statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (EXT.test(name)) out.push(p);
  }
  return out;
}

const roots = [
  path.join(siteRoot, "app"),
  path.join(siteRoot, "components"),
  path.join(siteRoot, "content"),
  path.join(siteRoot, "lib"),
  path.join(siteRoot, "public"),
  path.join(siteRoot, "scripts"),
  siteRoot, // package.json, next.config.ts, etc.
].filter((r) => fs.existsSync(r));

const files = [...new Set(roots.flatMap((r) => (fs.statSync(r).isDirectory() ? walk(r) : [r])))];

let failed = false;
for (const file of files.sort()) {
  if (file.includes(`${path.sep}node_modules${path.sep}`)) continue;
  if (path.resolve(file) === SELF) continue;
  const rel = path.relative(siteRoot, file);
  if (rel.startsWith("node_modules") || rel.startsWith(".next")) continue;
  const text = fs.readFileSync(file, "utf8");
  for (const { name, re } of BLOCKS) {
    re.lastIndex = 0;
    const m = re.exec(text);
    if (m) {
      failed = true;
      console.error(`[check-public] BLOCKED (${name}) in ${rel}: matched "${m[0]}"`);
    }
  }
}

if (failed) {
  console.error("\n[check-public] Fix redactions or false positives before shipping.\n");
  process.exit(1);
}
console.log("[check-public] OK — no blocklist matches in curated site paths.");
