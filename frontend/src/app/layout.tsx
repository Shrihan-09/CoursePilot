import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";

import { SiteFooter } from "@/components/shell/site-footer";
import { siteMetadata } from "@/lib/site-metadata";
import { THEME_SCRIPT } from "@/lib/theme";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = siteMetadata;

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#FFFFFF" },
    { media: "(prefers-color-scheme: dark)", color: "#0A0B0D" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning: THEME_SCRIPT sets the "dark" class before React
    // hydrates; that difference is intentional, not a bug.
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="flex min-h-dvh flex-col font-sans">
        <div className="flex-1">
          <Providers>{children}</Providers>
        </div>
        <SiteFooter />
      </body>
    </html>
  );
}
