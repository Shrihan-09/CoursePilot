"use client";

/** No shell/provider dependency: this also works when the root layout fails.
 * Never render the exception message/stack; those belong in server diagnostics.
 */
export function PageError({ onRetry }: { onRetry: () => void }) {
  return (
    <main style={{ maxWidth: 560, margin: "12vh auto", padding: 24, fontFamily: "system-ui, sans-serif" }}>
      <section role="alert" aria-labelledby="page-error-title">
        <p style={{ color: "#64748b", fontSize: 14 }}>CoursePilot</p>
        <h1 id="page-error-title" style={{ fontSize: 28, lineHeight: 1.2 }}>This page couldn&apos;t load</h1>
        <p style={{ lineHeight: 1.6 }}>Something went wrong while loading this page. Try again in a moment.</p>
        <button type="button" onClick={onRetry} style={{ minHeight: 44, border: 0, borderRadius: 8, padding: "10px 20px", background: "#0066e8", color: "white", font: "inherit", cursor: "pointer" }}>
          Try again
        </button>
        <a href="/home" style={{ display: "inline-block", marginLeft: 20, color: "inherit" }}>Back to Home</a>
      </section>
    </main>
  );
}
