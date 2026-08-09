import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";

export const metadata: Metadata = {
  title: "Method",
  description:
    "Why v1 overcomplexity failed, and how v2 backtests simple rules against paper capital before expanding.",
};

export default function ResearchPage() {
  const md = fs.readFileSync(path.join(process.cwd(), "content", "research.md"), "utf8");
  const examplesDir = path.join(process.cwd(), "public", "research-examples");
  const examples: string[] = [];
  if (fs.existsSync(examplesDir)) {
    for (const f of fs.readdirSync(examplesDir)) {
      if (f.endsWith(".svg")) examples.push(`/research-examples/${f}`);
    }
  }
  return (
    <div className="relative isolate min-h-screen">
      <div className="mx-auto max-w-3xl px-4 py-10 text-[15px] sm:px-6 sm:text-base lg:px-8">
        <SiteNav />
        <article className="prose prose-zinc max-w-none dark:prose-invert prose-headings:scroll-mt-24">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
        </article>
        {examples.length > 0 ? (
          <div className="mt-8 grid gap-4">
            {examples.map((src) => (
              <img key={src} src={src} alt="" className="rounded-lg border border-zinc-200 dark:border-zinc-700" />
            ))}
          </div>
        ) : (
          <p className="mt-6 text-sm text-zinc-500">
            Example chart assets can be placed under <code>site/public/research-examples/</code>.
          </p>
        )}
        <SiteFooter />
      </div>
    </div>
  );
}
