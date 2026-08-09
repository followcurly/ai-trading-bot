import Link from "next/link";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";

const features = [
  {
    href: "/flow",
    badge: "Interactive",
    title: "Flow",
    body: "Two scans/day. Red + underweight sleeve → buy. Green → skip. Hold.",
    cta: "Diagram",
    dotAccent: "bg-emerald-500",
  },
  {
    href: "/architecture",
    badge: "Docs",
    title: "Architecture",
    body: "ETF sleeves, Alpaca paper, journal — and what v1 left behind.",
    cta: "Read",
    dotAccent: "bg-sky-500",
  },
  {
    href: "/research",
    badge: "Method",
    title: "Receipts",
    body: "v1 −15.8% paper, v2 baseline, evolutionary north star.",
    cta: "Method",
    dotAccent: "bg-amber-500",
  },
];

const insidePoints = [
  "Three sleeves: core / dividend / growth",
  "Per-ticker red day → candidate buy",
  "10:30 & 15:30 ET scans",
  "No auto-sell, no LLM in the loop",
  "Paper equity is the scoreboard",
];

export default function Home() {
  return (
    <div className="relative isolate min-h-screen">
      <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6 lg:px-8">
        <SiteNav />

        <main className="space-y-12 pb-16 sm:space-y-16 sm:pb-20">
          <section className="space-y-6">
            <span className="inline-flex items-center gap-2 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-emerald-800 dark:border-emerald-400/30 dark:bg-emerald-500/15 dark:text-emerald-200">
              <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
              Public · educational · v2
            </span>
            <h1 className="text-4xl font-extrabold leading-[1.05] tracking-tight text-zinc-900 sm:text-5xl md:text-6xl dark:text-zinc-50">
              Simple rules.
              <br />
              <span className="italic text-zinc-800 dark:text-zinc-200">Real paper capital.</span>
            </h1>
            <p className="max-w-xl text-lg leading-relaxed text-zinc-700 dark:text-zinc-300">
              v1 overbuilt an LLM options desk and lost −15.8% paper. v2 buys quality ETFs on red
              days and holds. Next ambition: evolve rules that beat that baseline — not built yet.
            </p>

            <div className="flex flex-wrap items-center gap-3">
              <Link
                href="/flow"
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-zinc-900 px-5 py-2.5 text-sm font-semibold text-white shadow transition hover:bg-zinc-800 sm:flex-none dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200"
              >
                Flow <span aria-hidden>→</span>
              </Link>
              <Link
                href="/research"
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-zinc-300 bg-white px-5 py-2.5 text-sm font-semibold text-zinc-800 shadow-sm transition hover:bg-zinc-50 sm:flex-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800"
              >
                Method
              </Link>
            </div>

            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Educational only — not financial advice.
            </p>
          </section>

          <section className="grid gap-4 sm:grid-cols-2">
            <Link
              href="/research"
              className="block rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm transition hover:border-zinc-300 hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900/80 dark:hover:border-zinc-700"
            >
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500 dark:text-zinc-400">
                v1 paper
              </p>
              <p className="mt-2 text-lg font-bold text-zinc-900 dark:text-zinc-50">
                $100k → $84.2k (−15.8%)
              </p>
              <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-300">
                ~3 months · options-heavy · charts on Method →
              </p>
            </Link>

            <Link
              href="/research"
              className="block rounded-2xl border border-emerald-500/25 bg-emerald-500/5 p-5 shadow-sm transition hover:border-emerald-500/40 hover:shadow-md dark:bg-emerald-500/10"
            >
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-emerald-800 dark:text-emerald-200">
                Future · open
              </p>
              <p className="mt-2 text-lg font-bold text-zinc-900 dark:text-zinc-50">
                Mutate → score → keep
              </p>
              <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-300">
                MarI/O-style search on paper fitness. Shape undecided.
              </p>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/research-examples/v-future-evolve.svg"
                alt="Evolutionary loop sketch"
                className="mt-4 h-auto w-full rounded-lg border border-zinc-200/80 dark:border-zinc-700"
                loading="lazy"
              />
            </Link>
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
                Inside now
              </h3>
            </div>
            <ul className="grid gap-3 sm:grid-cols-2">
              {insidePoints.map((point) => (
                <li
                  key={point}
                  className="flex items-start gap-3 rounded-lg px-3 py-2 text-sm text-zinc-700 dark:text-zinc-300"
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
