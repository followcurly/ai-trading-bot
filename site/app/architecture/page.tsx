import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";

export const metadata: Metadata = {
  title: "Architecture",
  description:
    "Sanitized architecture reference: runtime topology, per-cycle pipeline, brain, risk, executor, journal, and viewer.",
};

export default function ArchitecturePage() {
  const md = fs.readFileSync(path.join(process.cwd(), "content", "architecture.md"), "utf8");
  return (
    <div className="relative isolate min-h-screen">
      <div className="mx-auto max-w-3xl px-4 py-10 text-[15px] sm:px-6 sm:text-base lg:px-8">
        <SiteNav />
        <article className="prose prose-zinc max-w-none dark:prose-invert prose-headings:scroll-mt-24 prose-headings:tracking-tight prose-a:text-indigo-600 prose-a:no-underline hover:prose-a:underline dark:prose-a:text-indigo-400 prose-code:before:content-none prose-code:after:content-none prose-code:rounded-md prose-code:bg-zinc-100 prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[0.85em] prose-code:font-medium dark:prose-code:bg-zinc-800 prose-table:block prose-table:overflow-x-auto prose-table:whitespace-nowrap">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
        </article>
        <SiteFooter />
      </div>
    </div>
  );
}
