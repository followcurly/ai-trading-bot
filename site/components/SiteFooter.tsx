import { GITHUB_MARK_PATH } from "@/lib/github-mark-path";

function GitHubIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d={GITHUB_MARK_PATH} />
    </svg>
  );
}

function LinkedInIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.86 0-2.14 1.45-2.14 2.95v5.66H9.34V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.38-1.85 3.62 0 4.29 2.38 4.29 5.47v6.27ZM5.34 7.43a2.06 2.06 0 1 1 0-4.12 2.06 2.06 0 0 1 0 4.12ZM7.12 20.45H3.55V9h3.57v11.45Z" />
    </svg>
  );
}

export function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-zinc-200 pt-8 text-sm text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
      <p className="mb-6 text-xs leading-relaxed text-zinc-500 dark:text-zinc-500">
        Educational documentation only — not financial advice, not a live trading interface, not
        an offer to provide trading services.
      </p>
      <div className="flex flex-wrap items-center gap-x-8 gap-y-4">
        <a
          href="https://github.com/followcurly/ai-trading-bot"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 font-medium text-zinc-800 underline-offset-4 hover:underline dark:text-zinc-200"
          aria-label="View source on GitHub"
        >
          <GitHubIcon className="h-5 w-5 shrink-0 text-zinc-700 dark:text-zinc-300" />
          GitHub
        </a>
        <a
          href="https://www.linkedin.com/in/diazebas/"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 font-medium text-zinc-800 underline-offset-4 hover:underline dark:text-zinc-200"
          aria-label="Sebastian on LinkedIn"
        >
          <LinkedInIcon className="h-5 w-5 shrink-0 text-[#0A66C2]" />
          LinkedIn
        </a>
      </div>
    </footer>
  );
}
