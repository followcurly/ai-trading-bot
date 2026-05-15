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

const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ??
  (process.env.VERCEL_URL ? `https://${process.env.VERCEL_URL}` : "http://localhost:3000");

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "AI Trading Bot — public architecture",
    template: "%s · AI Trading Bot",
  },
  description:
    "Educational, redacted architecture and interactive Mermaid flow for a research-style trading stack.",
  openGraph: {
    title: "AI Trading Bot — public architecture & flow",
    description:
      "Sanitized diagrams and prose: feeds → pipeline → sinks. Not live trading; no secrets.",
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
