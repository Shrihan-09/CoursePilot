"use client";

import { PageError } from "@/components/feedback/page-error";

/** Root-layout failures need their own html/body and provider-free styling. */
export default function GlobalError({}: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, background: "#f8fafc", color: "#0f172a" }}>
        <PageError onRetry={() => window.location.reload()} />
      </body>
    </html>
  );
}
