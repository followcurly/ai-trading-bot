import fs from "node:fs";
import path from "node:path";
import type { Metadata } from "next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";

export const metadata: Metadata = {
  title: "Method",
  description: "v1 paper postmortem, v2 baseline, and an open-ended evolutionary direction.",
};

export default function ResearchPage() {
  const md = fs.readFileSync(path.join(process.cwd(), "content", "research.md"), "utf8");
  return (
    <div className="relative isolate min-h-screen">
      <div className="mx-auto max-w-3xl px-4 py-10 text-[15px] sm:px-6 sm:text-base lg:px-8">
        <SiteNav />
        <article className="prose prose-zinc max-w-none dark:prose-invert prose-headings:scroll-mt-24">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              h2: ({ children }) => {
                const text = Array.isArray(children)
                  ? children.map(String).join("")
                  : String(children ?? "");
                const id = text
                  .toLowerCase()
                  .replace(/[^a-z0-9]+/g, "-")
                  .replace(/^-|-$/g, "");
                return (
                  <h2 id={id} className="scroll-mt-24">
                    {children}
                  </h2>
                );
              },
              img: ({ src, alt }) => {
                if (!src || typeof src !== "string") return null;
                return (
                  // Native img: next/image breaks static SVGs in this markdown path.
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={src}
                    alt={alt || ""}
                    className="not-prose my-6 h-auto w-full rounded-xl border border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900/80"
                    loading="lazy"
                  />
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
