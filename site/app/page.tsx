import Link from "next/link";
import { SiteNav } from "@/components/SiteNav";

const features = [
  {
    href: "/flow",
    badge: "Interactive",
    title: "Pipeline flow",
    body: "Pan, zoom, and click any node in a Mermaid diagram of the per-cycle pipeline — external feeds, regime, snapshot, brain, risk, executor, and sinks.",
    cta: "Open the diagram",
    accent: "from-indigo-500/20 via-violet-500/10 to-fuchsia-500/10",
    dotAccent: "bg-indigo-500",
  },
  {
    href: "/architecture",
    badge: "Long-form",
    title: "Architecture prose",
    body: "Sanitized walkthrough: runtime topology, market snapshot, model decision flow, risk engine policies, executor guards, journal layout, weekly reporting.",
    cta: "Read the doc",
    accent: "from-emerald-500/20 via-teal-500/10 to-sky-500/10",
    dotAccent: "bg-emerald-500",
  },
];

const insidePoints = [
  "Cross-asset regime pre-brain (TTL-cached cross-asset verdict)",
  "Watchlist construction: screener + anchors + bearish hedges + held positions",
  "Snapshot builder with data-quality and sector context",
  "Tier-1 / Tier-2 risk policies and intraday giveback halts",
  "Executor: bracketed equity orders + single-leg options + liquidity gate",
  "Journal: SQLite + JSONL with size-based rotation and WAL concurrency",
  "Weekly Sonnet review → human blog + trader feedback bullets",
];

export default function Home() {
  return (
    <div className="relative isolate min-h-screen overflow-hidden">
      <div className="pointer-events-none absolute inset-x-0 -top-40 -z-10 h-[34rem] bg-gradient-to-br from-indigo-500/15 via-violet-500/10 to-transparent blur-3xl" />
      <div className="pointer-events-none absolute right-0 top-40 -z-10 h-[24rem] w-[24rem] rounded-full bg-emerald-500/10 blur-3xl" />
      <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6 lg:px-8">
        <SiteNav />

        <main className="space-y-16 pb-20">
          <section className="space-y-8">
            <span className="inline-flex items-center gap-2 rounded-full border border-indigo-500/20 bg-indigo-500/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-700 dark:border-indigo-400/30 dark:bg-indigo-500/15 dark:text-indigo-200">
              <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-500" />
              Public · educational · sanitized
            </span>
            <h1 className="bg-gradient-to-br from-zinc-900 via-zinc-800 to-zinc-500 bg-clip-text text-5xl font-extrabold leading-[1.05] tracking-tight text-transparent dark:from-white dark:via-zinc-200 dark:to-zinc-400 sm:text-6xl md:text-7xl">
              An AI trading bot,
              <br />
              <span className="bg-gradient-to-br from-indigo-500 via-violet-500 to-fuchsia-500 bg-clip-text text-transparent">
                pipeline-first.
              </span>
            </h1>
            <p className="max-w-2xl text-lg leading-relaxed text-zinc-700 dark:text-zinc-300">
              Curated diagrams and prose for a research-grade equity + options stack: cross-asset
              regime, per-symbol snapshots, model decisions, risk gating, broker execution, and
              durable journaling.{" "}
              <span className="text-zinc-500 dark:text-zinc-400">
                No live account. No live secrets. No live signals.
              </span>
            </p>

            <div className="flex flex-wrap items-center gap-3">
              <Link
                href="/flow"
                className="group relative inline-flex items-center gap-2 overflow-hidden rounded-lg bg-zinc-900 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-500/20 transition hover:shadow-indigo-500/40 dark:bg-white dark:text-zinc-900"
              >
                <span className="absolute inset-0 -z-10 bg-gradient-to-r from-indigo-500 via-violet-500 to-fuchsia-500 opacity-0 transition group-hover:opacity-100" />
                <span className="transition group-hover:text-white">Explore the flow</span>
                <span aria-hidden className="transition group-hover:translate-x-0.5 group-hover:text-white">
                  →
                </span>
              </Link>
              <Link
                href="/architecture"
                className="inline-flex items-center gap-2 rounded-lg border border-zinc-300 bg-white/60 px-5 py-2.5 text-sm font-semibold text-zinc-800 backdrop-blur transition hover:bg-white hover:shadow-sm dark:border-zinc-700 dark:bg-zinc-900/60 dark:text-zinc-100 dark:hover:bg-zinc-900"
              >
                Read the architecture
              </Link>
            </div>

            <div className="rounded-xl border border-amber-300/40 bg-amber-50/60 p-4 text-sm text-amber-950 backdrop-blur dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100">
              <strong>Disclaimer:</strong> educational documentation only. This is not financial
              advice, not a live trading interface, and not an offer to provide trading services.
            </div>
          </section>

          <section className="grid gap-5 sm:grid-cols-2">
            {features.map((f) => (
              <Link
                key={f.href}
                href={f.href}
                className="group relative overflow-hidden rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:shadow-lg dark:border-zinc-800 dark:bg-zinc-900/80"
              >
                <div
                  className={`pointer-events-none absolute -right-16 -top-16 h-48 w-48 rounded-full bg-gradient-to-br ${f.accent} blur-2xl transition group-hover:scale-110`}
                />
                <div className="relative space-y-3">
                  <div className="flex items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${f.dotAccent}`} />
                    <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500 dark:text-zinc-400">
                      {f.badge}
                    </span>
                  </div>
                  <h2 className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">{f.title}</h2>
                  <p className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-300">
                    {f.body}
                  </p>
                  <p className="pt-2 text-sm font-semibold text-zinc-900 transition group-hover:translate-x-0.5 dark:text-zinc-100">
                    {f.cta} <span aria-hidden>→</span>
                  </p>
                </div>
              </Link>
            ))}
          </section>

          <section className="rounded-2xl border border-zinc-200 bg-white/60 p-6 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/60 sm:p-8">
            <div className="mb-5 flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-violet-500" />
              <h3 className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500 dark:text-zinc-400">
                What&apos;s inside
              </h3>
            </div>
            <ul className="grid gap-3 sm:grid-cols-2">
              {insidePoints.map((point) => (
                <li
                  key={point}
                  className="flex items-start gap-3 rounded-lg border border-transparent px-3 py-2 text-sm text-zinc-700 transition hover:border-zinc-200 hover:bg-white dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:bg-zinc-900"
                >
                  <span aria-hidden className="mt-1 text-violet-500">
                    ◆
                  </span>
                  <span>{point}</span>
                </li>
              ))}
            </ul>
          </section>

          <footer className="border-t border-zinc-200 pt-6 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
            Source on{" "}
            <a
              href="https://github.com/followcurly/ai-trading-bot"
              className="font-medium text-zinc-700 underline-offset-2 hover:underline dark:text-zinc-200"
              target="_blank"
              rel="noreferrer"
            >
              github.com/followcurly/ai-trading-bot
            </a>
            . Diagrams and prose are sanitized public copies — bot internals stay private.
          </footer>
        </main>
      </div>
    </div>
  );
}
