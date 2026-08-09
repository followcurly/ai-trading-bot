import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import { FlowDiagram } from "@/components/FlowDiagram";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";

export const metadata: Metadata = {
  title: "Flow diagram",
  description:
    "Pan/zoom Mermaid diagram of the v2 red-day buy-and-hold loop — public, redacted copy.",
};

export default function FlowPage() {
  const source = fs.readFileSync(path.join(process.cwd(), "content", "flow.mmd"), "utf8");
  return (
    <div className="relative isolate min-h-screen">
      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
        <SiteNav />
        <header className="mb-6 max-w-3xl space-y-2">
          <span className="inline-flex items-center gap-2 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-emerald-800 dark:border-emerald-400/30 dark:bg-emerald-500/15 dark:text-emerald-200">
            Interactive diagram · v2
          </span>
          <h1 className="text-3xl font-bold tracking-tight text-zinc-900 dark:text-zinc-50 sm:text-4xl">
            Red-day flow
          </h1>
          <p className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
            Schematic only. No LLM nodes. Click a box for a short plain-language note. Solid
            arrows are the primary path from schedule → red check → sleeve weight → buy or hold →
            journal and paper broker.
          </p>
        </header>
        <FlowDiagram source={source} />
        <SiteFooter />
      </div>
    </div>
  );
}
