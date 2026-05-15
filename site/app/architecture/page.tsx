import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SiteNav } from "@/components/SiteNav";

export const metadata: Metadata = {
  title: "Architecture",
  description:
    "Sanitized architecture reference: runtime topology, per-cycle pipeline, brain, risk, executor, journal, and viewer.",
};

export default function ArchitecturePage() {
  const md = fs.readFileSync(path.join(process.cwd(), "content", "architecture.md"), "utf8");
  return (
    <div className="relative isolate min-h-screen overflow-hidden">
      <div className="pointer-events-none absolute inset-x-0 -top-40 -z-10 h-[28rem] bg-gradient-to-br from-emerald-500/10 via-teal-500/5 to-transparent blur-3xl" />
      <div className="mx-auto max-w-3xl px-4 py-10 sm:px-6 lg:px-8">
        <SiteNav />
        <article className="prose prose-zinc max-w-none dark:prose-invert prose-headings:scroll-mt-24 prose-headings:tracking-tight prose-h1:bg-gradient-to-br prose-h1:from-zinc-900 prose-h1:via-zinc-700 prose-h1:to-zinc-500 prose-h1:bg-clip-text prose-h1:text-transparent dark:prose-h1:from-white dark:prose-h1:via-zinc-200 dark:prose-h1:to-zinc-400 prose-a:text-indigo-600 prose-a:no-underline hover:prose-a:underline dark:prose-a:text-indigo-400 prose-code:before:content-none prose-code:after:content-none prose-code:rounded-md prose-code:bg-zinc-100 prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[0.85em] prose-code:font-medium dark:prose-code:bg-zinc-800">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
        </article>
      </div>
    </div>
  );
}
