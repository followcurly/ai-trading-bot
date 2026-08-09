import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import Image from "next/image";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";

export const metadata: Metadata = {
  title: "Method",
  description:
    "v1 paper results and postmortem, plus how v2 backtests simple rules against paper capital before expanding.",
};

export default function ResearchPage() {
  const md = fs.readFileSync(path.join(process.cwd(), "content", "research.md"), "utf8");
  return (
    <div className="relative isolate min-h-screen">
      <div className="mx-auto max-w-3xl px-4 py-10 text-[15px] sm:px-6 sm:text-base lg:px-8">
        <SiteNav />
        <article className="prose prose-zinc max-w-none dark:prose-invert prose-headings:scroll-mt-24 prose-img:my-6 prose-img:rounded-xl prose-img:border prose-img:border-zinc-200 dark:prose-img:border-zinc-700">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              img: ({ src, alt }) => {
                if (!src || typeof src !== "string") return null;
                return (
                  <span className="not-prose my-6 block overflow-hidden rounded-xl border border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900">
                    {/* Public SVGs are static; next/image still needs width/height for layout */}
                    <Image
                      src={src}
                      alt={alt || ""}
                      width={720}
                      height={320}
                      className="h-auto w-full"
                      unoptimized
                    />
                  </span>
                );
              },
            }}
          >
            {md}
          </ReactMarkdown>
        </article>
        <SiteFooter />
      </div>
    </div>
  );
}
