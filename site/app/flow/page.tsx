import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import { FlowDiagram } from "@/components/FlowDiagram";
import { SiteNav } from "@/components/SiteNav";

export const metadata: Metadata = {
  title: "Flow diagram",
  description:
    "Pan/zoom Mermaid diagram of feeds, pipeline, sinks, and weekly reporting — public, redacted copy.",
};

export default function FlowPage() {
  const source = fs.readFileSync(path.join(process.cwd(), "content", "flow.mmd"), "utf8");
  return (
    <div className="mx-auto max-w-7xl px-4 py-10">
      <SiteNav />
      <h1 className="mb-2 text-2xl font-bold text-zinc-900 dark:text-zinc-50">Pipeline flow</h1>
      <p className="mb-6 max-w-3xl text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
        Schematic only. Solid arrows are primary flow; dashed arrows are regime attachment, health
        pings, historical reads, or optional paths. The dashed link from <strong>regime</strong> to{" "}
        <strong>get_market_snapshot</strong> reflects benchmark equity slices used when assembling
        cross-asset context (alongside the regime verdict attached to the snapshot dict).
      </p>
      <FlowDiagram source={source} />
    </div>
  );
}
