import Link from "next/link";
import { SiteNav } from "@/components/SiteNav";

export default function Home() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-10">
      <SiteNav />
      <main className="space-y-6">
        <h1 className="text-3xl font-bold tracking-tight text-zinc-900 dark:text-zinc-50">
          Public architecture &amp; flow
        </h1>
        <p className="text-lg leading-relaxed text-zinc-700 dark:text-zinc-300">
          Curated, redacted views of how the research trading stack fits together: external feeds, the
          per-cycle pipeline, sinks, and the weekly reporting path. Built for LinkedIn / portfolio
          sharing — <strong>not</strong> connected to any live account or secrets.
        </p>
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-100">
          <strong>Disclaimer:</strong> educational documentation only. This is not financial advice,
          not a live trading interface, and not an offer to provide trading services.
        </div>
        <div className="flex flex-wrap gap-3">
          <Link
            href="/flow"
            className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white shadow hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
          >
            Interactive flow
          </Link>
          <Link
            href="/architecture"
            className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-800 hover:bg-zinc-50 dark:border-zinc-600 dark:text-zinc-100 dark:hover:bg-zinc-800"
          >
            Architecture prose
          </Link>
        </div>
      </main>
    </div>
  );
}
