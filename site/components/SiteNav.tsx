import Link from "next/link";
import { GITHUB_MARK_PATH } from "@/lib/github-mark-path";

const links = [
  { href: "/", label: "Home" },
  { href: "/flow", label: "Flow" },
  { href: "/architecture", label: "Architecture" },
];

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

const iconLink =
  "rounded-md p-2 text-zinc-600 transition hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100";

export function SiteNav() {
  return (
    <header className="mb-8 flex flex-wrap items-center justify-between gap-3 border-b border-zinc-200/80 pb-4 dark:border-zinc-800/80 sm:gap-4">
      <Link
        href="/"
        className="group inline-flex min-w-0 items-center gap-2 font-semibold tracking-tight text-zinc-900 dark:text-zinc-50"
      >
        <span className="relative inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-zinc-900 text-[11px] font-bold text-white shadow-sm dark:bg-zinc-100 dark:text-zinc-900">
          ai
        </span>
        <span className="truncate">
          AI Trading Bot{" "}
          <span className="hidden text-zinc-500 sm:inline dark:text-zinc-400">· public</span>
        </span>
      </Link>
      <div className="flex flex-wrap items-center gap-1 sm:gap-2">
        <nav className="flex flex-wrap gap-0.5 text-sm font-medium sm:gap-1">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="rounded-md px-2 py-1 text-zinc-600 transition hover:bg-zinc-100 hover:text-zinc-900 sm:px-3 sm:py-1.5 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-white"
            >
              {l.label}
            </Link>
          ))}
        </nav>
        <span className="hidden h-4 w-px bg-zinc-200 sm:block dark:bg-zinc-700" aria-hidden />
        <div className="flex items-center gap-0.5">
          <a
            href="https://github.com/followcurly/ai-trading-bot"
            target="_blank"
            rel="noreferrer"
            className={iconLink}
            aria-label="GitHub repository"
          >
            <GitHubIcon className="h-4 w-4" />
          </a>
          <a
            href="https://www.linkedin.com/in/diazebas/"
            target="_blank"
            rel="noreferrer"
            className={iconLink}
            aria-label="LinkedIn profile"
          >
            <LinkedInIcon className="h-4 w-4" />
          </a>
        </div>
      </div>
    </header>
  );
}
