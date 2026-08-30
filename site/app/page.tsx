import Link from "next/link";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";

const chapters = [
  {
    href: "/research",
    step: "01",
    title: "I tried a desk",
    body: "I built an LLM options stack that looked clever on paper.",
    visual: "/research-examples/v1-pipeline.svg",
    accent: "border-rose-500/30",
  },
  {
    href: "/research",
    step: "02",
    title: "I ended −15.8%",
    body: "$100k to $84.2k paper. Receipts beat vibes.",
    visual: "/research-examples/v1-equity-curve.svg",
    accent: "border-amber-500/30",
  },
  {
    href: "/flow",
    step: "03",
    title: "I start here",
    body: "Red-day ETFs, hold, no model in the loop.",
    visual: "/research-examples/v2-loop.svg",
    accent: "border-emerald-500/30",
  },
  {
    href: "/research#i-checked-the-scoreboard",
    step: "04",
    title: "I checked the scoreboard",
    body: "+1.2% paper in four weeks. S&P hold +3.0%. Still the bar.",
    visual: "/research-examples/v2-vs-spy-score.svg",
    accent: "border-teal-500/30",
  },
  {
    href: "/research#where-i-hope-to-go",
    step: "05",
    title: "I hope to learn",
    body: "Evolve rules on paper fitness — then earn LLM back.",
    visual: "/research-examples/v-future-evolve.svg",
    accent: "border-sky-500/30",
  },
];

const measures = [
  "Paper equity from the ~$84k v2 line",
  "Same-dollar S&P hold (day-one SPY)",
  "Drawdown from peak",
  "Buys vs the one-per-sleeve rule",
  "Sleeve weights (50 / 25 / 25)",
  "Journal receipts every scan",
];

export default function Home() {
  return (
    <div className="relative isolate min-h-screen">
      <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6 lg:px-8">
        <SiteNav />

        <main className="space-y-14 pb-16 sm:space-y-20 sm:pb-20">
          <section className="space-y-6">
            <span className="inline-flex items-center gap-2 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-emerald-800 dark:border-emerald-400/30 dark:bg-emerald-500/15 dark:text-emerald-200">
              <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
              My paper lab · educational
            </span>
            <h1 className="max-w-3xl text-4xl font-extrabold leading-[1.05] tracking-tight text-zinc-900 sm:text-5xl md:text-6xl dark:text-zinc-50">
              I tried to automate a desk.
              <br />
              <span className="italic text-zinc-600 dark:text-zinc-300">
                I am starting over simpler.
              </span>
            </h1>
            <p className="max-w-2xl text-lg leading-relaxed text-zinc-700 dark:text-zinc-300">
              I built a complex LLM options bot, lost −15.8% on paper, and kept the same book. Now I
              buy quality ETFs on red days and hold. Four weeks in I am +1.2% — and still behind a
              same-dollar S&amp;P hold. I hope to evolve better rules — and learn the LLM piece again
              as I iterate.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <Link
                href="/research"
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-zinc-900 px-5 py-2.5 text-sm font-semibold text-white shadow transition hover:bg-zinc-800 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200"
              >
                Read my method <span aria-hidden>→</span>
              </Link>
              <Link
                href="/flow"
                className="inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-300 bg-white px-5 py-2.5 text-sm font-semibold text-zinc-800 shadow-sm transition hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800"
              >
                See what I run now
              </Link>
            </div>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Educational only — not financial advice.
            </p>
          </section>

          <section className="space-y-4">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/research-examples/story-arc.svg"
              alt="Story arc: tried, built, ended, now, scoreboard, next, hope"
              className="h-auto w-full rounded-2xl border border-zinc-200 dark:border-zinc-800"
              loading="eager"
            />
          </section>

          <section className="grid gap-4 sm:grid-cols-2">
            {chapters.map((c) => (
              <Link
                key={c.step}
                href={c.href}
                className={`group overflow-hidden rounded-2xl border bg-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-lg dark:bg-zinc-900/80 ${c.accent} border-zinc-200 dark:border-zinc-800`}
              >
                <div className="space-y-3 p-5">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500 dark:text-zinc-400">
                    {c.step}
                  </p>
                  <h2 className="text-xl font-bold text-zinc-900 dark:text-zinc-50">{c.title}</h2>
                  <p className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-300">{c.body}</p>
                </div>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={c.visual}
                  alt=""
                  className="h-auto w-full border-t border-zinc-200/80 dark:border-zinc-800"
                  loading="lazy"
                />
              </Link>
            ))}
          </section>

          <section className="grid gap-6 rounded-2xl border border-zinc-200 bg-white/80 p-6 dark:border-zinc-800 dark:bg-zinc-900/60 sm:grid-cols-2 sm:p-8">
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500 dark:text-zinc-400">
                What I measure
              </h3>
              <ul className="mt-4 space-y-3">
                {measures.map((m) => (
                  <li key={m} className="flex items-start gap-3 text-sm text-zinc-700 dark:text-zinc-300">
                    <span aria-hidden className="mt-1 text-emerald-500">
                      ◆
                    </span>
                    <span>{m}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex flex-col justify-between gap-4">
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500 dark:text-zinc-400">
                  Where I hope to get
                </h3>
                <p className="mt-4 text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
                  A mutator that beats this boring baseline on paper — then, as I iterate, I want to
                  understand when an LLM belongs in the loop again, instead of assuming it from day
                  one.
                </p>
              </div>
              <Link
                href="/architecture"
                className="text-sm font-semibold text-zinc-900 underline-offset-4 hover:underline dark:text-zinc-100"
              >
                How I wired v2 →
              </Link>
            </div>
          </section>

          <SiteFooter />
        </main>
      </div>
    </div>
  );
}
