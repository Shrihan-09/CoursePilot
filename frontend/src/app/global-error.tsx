"use client";

import "./globals.css";

import { PageError } from "@/components/feedback/page-error";
import { THEME_SCRIPT } from "@/lib/theme";

/** Root-layout failures need their own html/body; the stylesheet is imported here directly. */
export default function GlobalError({}: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-dvh font-sans">
        <PageError onRetry={() => window.location.reload()} />
      </body>
    </html>
  );
}
