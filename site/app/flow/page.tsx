import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import { FlowDiagram } from "@/components/FlowDiagram";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";

export const metadata: Metadata = {
  title: "Flow diagram",
  description:
    "Pan/zoom Mermaid diagram of feeds, pipeline, sinks, and weekly reporting — public, redacted copy.",
};

export default function FlowPage() {
  const source = fs.readFileSync(path.join(process.cwd(), "content", "flow.mmd"), "utf8");
  return (
    <div className="relative isolate min-h-screen">
      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
        <SiteNav />
        <header className="mb-6 max-w-3xl space-y-2">
          <span className="inline-flex items-center gap-2 rounded-full border border-indigo-500/20 bg-indigo-500/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-indigo-700 dark:border-indigo-400/30 dark:bg-indigo-500/15 dark:text-indigo-200">
            Interactive diagram
          </span>
          <h1 className="text-3xl font-bold tracking-tight text-zinc-900 dark:text-zinc-50 sm:text-4xl">
            Pipeline flow
          </h1>
          <p className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
            Schematic only. Solid arrows are primary flow; dashed arrows are regime attachment,
            health pings, historical reads, or optional paths. The dashed link from{" "}
            <strong className="text-zinc-800 dark:text-zinc-100">regime</strong> to{" "}
            <strong className="text-zinc-800 dark:text-zinc-100">get_market_snapshot</strong>{" "}
            reflects benchmark equity slices used when assembling cross-asset context.
          </p>
        </header>
        <FlowDiagram source={source} />
        <SiteFooter />
      </div>
    </div>
  );
}
