"use client";

import { PageError } from "@/components/feedback/page-error";
import { AFFILIATION_DISCLAIMER } from "@/lib/brand";

/** Root-layout failures need their own html/body and provider-free styling. */
export default function GlobalError({}: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, background: "#f8fafc", color: "#0f172a" }}>
        <PageError onRetry={() => window.location.reload()} />
        {/* The root layout (and its footer) failed, so the notice is repeated here. */}
        <footer style={{ padding: 16, textAlign: "center", fontSize: 12, color: "#64748b", fontFamily: "system-ui, sans-serif" }}>
          {AFFILIATION_DISCLAIMER}
        </footer>
      </body>
    </html>
  );
}
