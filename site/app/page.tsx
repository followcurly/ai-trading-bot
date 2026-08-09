import Link from "next/link";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";

const features = [
  {
    href: "/flow",
    badge: "Interactive",
    title: "Simple flow",
    body: "Two scans a day. Each fund is checked on its own day return. Red + underweight sleeve → buy. Green → skip. Hold. No model in the loop.",
    cta: "Open the diagram",
    dotAccent: "bg-emerald-500",
  },
  {
    href: "/architecture",
    badge: "Long-form",
    title: "Architecture",
    body: "How the red-day buy-and-hold stack is wired: fund sleeves, Alpaca paper, journal, and what was deliberately left out after v1.",
    cta: "Read the doc",
    dotAccent: "bg-sky-500",
  },
  {
    href: "/research",
    badge: "Method",
    title: "Lessons & method",
    body: "Why the LLM pipeline failed, and how simple rules get backtested against real paper capital before anything expands.",
    cta: "Read the method",
    dotAccent: "bg-amber-500",
  },
];

const insidePoints = [
  "Fixed ETF universe in three sleeves (core / dividend / growth)",
  "Per-ticker red check (prior close → last) — no SPY-only gate",
  "Buy only when that fund is red and its sleeve is underweight",
  "Scans at 10:30 and 15:30 ET so late-day red can still matter",
  "Same sleeve not bought twice in one session",
  "Hold forever in software — no auto-sell, no trail ladder, no LLM",
  "Journal + read-only dashboard for receipts; paper capital stays the scoreboard",
];

export default function Home() {
  return (
    <div className="relative isolate min-h-screen">
      <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6 lg:px-8">
        <SiteNav />

        <main className="space-y-12 pb-16 sm:space-y-16 sm:pb-20">
          <section className="space-y-8">
            <span className="inline-flex items-center gap-2 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-emerald-800 dark:border-emerald-400/30 dark:bg-emerald-500/15 dark:text-emerald-200">
              <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
              Public · educational · v2 simplified
            </span>
            <h1 className="text-4xl font-extrabold leading-[1.05] tracking-tight text-zinc-900 sm:text-5xl md:text-6xl lg:text-7xl dark:text-zinc-50">
              Simple rules.
              <br />
              <span className="italic text-zinc-800 dark:text-zinc-200">Real paper capital.</span>
            </h1>
            <p className="max-w-2xl text-lg leading-relaxed text-zinc-700 dark:text-zinc-300">
              The first version of this bot was a failure of overcomplexity — regime calls, model
              debates, options ladders, weekly Sonnet postmortems. It did not trust time-tested
              methods. v2 starts smaller: buy quality ETFs on red days, hold, and grow the system
              only after simple scenarios prove themselves against the live paper book.
            </p>

            <div className="flex flex-wrap items-center gap-3">
              <Link
                href="/flow"
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-zinc-900 px-5 py-2.5 text-sm font-semibold text-white shadow transition hover:bg-zinc-800 sm:flex-none dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200"
              >
                See the flow <span aria-hidden>→</span>
              </Link>
              <Link
                href="/architecture"
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-zinc-300 bg-white px-5 py-2.5 text-sm font-semibold text-zinc-800 shadow-sm transition hover:bg-zinc-50 sm:flex-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800"
              >
                Read the architecture
              </Link>
            </div>

            <div className="rounded-xl border border-amber-300/40 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-100">
              <strong>Disclaimer:</strong> educational documentation only. This is not financial
              advice, not a live trading interface, and not an offer to provide trading services.
            </div>
          </section>

          <section className="grid gap-5 sm:grid-cols-3">
            {features.map((f) => (
              <Link
                key={f.href}
                href={f.href}
                className="group relative overflow-hidden rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-lg sm:p-6 dark:border-zinc-800 dark:bg-zinc-900/80"
              >
                <div className="relative space-y-3">
                  <div className="flex items-center gap-2">
                    <span className={`h-2 w-2 shrink-0 rounded-full ${f.dotAccent}`} />
                    <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500 dark:text-zinc-400">
                      {f.badge}
                    </span>
                  </div>
                  <h2 className="text-xl font-bold text-zinc-900 dark:text-zinc-50">{f.title}</h2>
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

          <section className="rounded-2xl border border-zinc-200 bg-white/80 p-6 dark:border-zinc-800 dark:bg-zinc-900/60 sm:p-8">
            <div className="mb-5 flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              <h3 className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500 dark:text-zinc-400">
                What&apos;s inside now
              </h3>
            </div>
            <ul className="grid gap-3 sm:grid-cols-2">
              {insidePoints.map((point) => (
                <li
                  key={point}
                  className="flex items-start gap-3 rounded-lg border border-transparent px-3 py-2 text-sm text-zinc-700 transition hover:border-zinc-200 hover:bg-white dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:bg-zinc-900"
                >
                  <span aria-hidden className="mt-1 text-emerald-500">
                    ◆
                  </span>
                  <span>{point}</span>
                </li>
              ))}
            </ul>
          </section>

          <SiteFooter />
        </main>
      </div>
    </div>
  );
}
