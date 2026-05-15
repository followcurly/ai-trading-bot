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
    <div className="mx-auto max-w-3xl px-4 py-10">
      <SiteNav />
      <article className="prose prose-zinc max-w-none dark:prose-invert prose-headings:scroll-mt-24 prose-a:text-blue-600 prose-a:no-underline hover:prose-a:underline dark:prose-a:text-blue-400">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
      </article>
    </div>
  );
}
