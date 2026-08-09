import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

/** Canonical site origin for metadataBase / OG (no trailing slash). */
function siteOrigin(): string {
  const explicit = process.env.NEXT_PUBLIC_SITE_URL?.trim();
  if (explicit) return explicit.replace(/\/$/, "");
  // Shortest production custom domain when assigned (set on Vercel even on preview builds).
  const prod = process.env.VERCEL_PROJECT_PRODUCTION_URL?.trim();
  if (prod) return `https://${prod.replace(/^https?:\/\//, "").replace(/\/$/, "")}`;
  const vercel = process.env.VERCEL_URL?.trim();
  if (vercel) return `https://${vercel.replace(/^https?:\/\//, "")}`;
  return "http://localhost:3000";
}

const siteUrl = siteOrigin();

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "AI Trading Bot — simple rules, paper capital",
    template: "%s · AI Trading Bot",
  },
  description:
    "Educational site for a simplified red-day ETF buy-and-hold paper experiment. Not financial advice.",
  openGraph: {
    title: "AI Trading Bot — simple rules, paper capital",
    description:
      "v2: buy quality ETFs on red days, hold, expand only after simple scenarios earn it. Not live trading.",
    type: "website",
    url: "/",
  },
  twitter: {
    card: "summary_large_image",
    title: "AI Trading Bot — public architecture",
    description: "Interactive flow + architecture reference (curated, educational).",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} min-h-screen bg-[var(--background)] font-sans text-[var(--foreground)] antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
