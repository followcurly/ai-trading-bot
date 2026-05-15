import Link from "next/link";

const links = [
  { href: "/", label: "Home" },
  { href: "/flow", label: "Flow" },
  { href: "/architecture", label: "Architecture" },
];

export function SiteNav() {
  return (
    <header className="mb-8 flex flex-wrap items-center justify-between gap-4 border-b border-zinc-200/80 pb-4 dark:border-zinc-800/80">
      <Link
        href="/"
        className="group inline-flex items-center gap-2 font-semibold tracking-tight text-zinc-900 dark:text-zinc-50"
      >
        <span className="relative inline-flex h-6 w-6 items-center justify-center rounded-md bg-gradient-to-br from-indigo-500 via-violet-500 to-fuchsia-500 text-[11px] font-bold text-white shadow-sm">
          ai
        </span>
        <span>
          AI Trading Bot{" "}
          <span className="text-zinc-500 dark:text-zinc-400">· public</span>
        </span>
      </Link>
      <nav className="flex flex-wrap gap-1 text-sm font-medium">
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="rounded-md px-3 py-1.5 text-zinc-600 transition hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-white"
          >
            {l.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
