import Link from "next/link";

const links = [
  { href: "/", label: "Home" },
  { href: "/flow", label: "Flow" },
  { href: "/architecture", label: "Architecture" },
];

export function SiteNav() {
  return (
    <header className="mb-8 flex flex-wrap items-center justify-between gap-4 border-b border-zinc-200 pb-4 dark:border-zinc-800">
      <Link href="/" className="font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
        AI Trading Bot <span className="text-zinc-500 dark:text-zinc-400">· public</span>
      </Link>
      <nav className="flex flex-wrap gap-3 text-sm font-medium">
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="rounded-md px-2 py-1 text-zinc-600 transition hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-white"
          >
            {l.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
