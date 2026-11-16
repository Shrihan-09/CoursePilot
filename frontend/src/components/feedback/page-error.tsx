"use client";

/**
 * Full-page error / not-found card (UI polish A5). No shell or provider
 * dependency, so it also works when the root layout fails (global-error).
 * Never renders the exception message or stack; those belong in server
 * diagnostics - and never a bare "Internal Server Error".
 */
export function StatusPage({
  code,
  title,
  body,
  primary,
  secondary,
}: {
  code?: string;
  title: string;
  body: string;
  primary: { label: string; onClick?: () => void; href?: string };
  secondary?: { label: string; href: string };
}) {
  const primaryClass = "inline-flex min-h-11 items-center justify-center gap-2 rounded bg-primary px-5 text-sm font-semibold text-primary-foreground shadow-[0_0_0_1px_rgb(var(--glow-accent)/.25),0_0_18px_-4px_rgb(var(--glow-accent)/.45)] hover:bg-primary-hover";
  const secondaryClass = "inline-flex min-h-11 items-center justify-center rounded border border-border-strong bg-surface-elevated px-5 text-sm font-medium text-fg hover:bg-surface-hover";
  return (
    <main className="flex min-h-dvh items-center justify-center bg-background px-4 py-12 text-fg">
      <section role={code === "404" ? undefined : "alert"} aria-labelledby="status-title"
        className="w-full max-w-md space-y-5 rounded-xl border border-border bg-surface-elevated p-6 text-center shadow-elevated sm:p-8">
        {/* eslint-disable-next-line @next/next/no-img-element -- static brand asset; works without the image pipeline */}
        <img src="/brand/coursepilot-mark-128.png" alt="" width={48} height={48} className="mx-auto size-12" />
        <div className="space-y-2">
          {code && <p className="tabular text-xs font-semibold uppercase tracking-[0.12em] text-fg-muted">Error {code}</p>}
          <h1 id="status-title" className="text-2xl font-bold tracking-tight">{title}</h1>
          <p className="text-sm text-fg-secondary">{body}</p>
        </div>
        <div className="flex flex-col justify-center gap-2 sm:flex-row">
          {primary.href ? (
            <a href={primary.href} className={primaryClass}>{primary.label}</a>
          ) : (
            <button type="button" onClick={primary.onClick} className={primaryClass}>{primary.label}</button>
          )}
          {secondary && <a href={secondary.href} className={secondaryClass}>{secondary.label}</a>}
        </div>
      </section>
    </main>
  );
}

export function PageError({ onRetry }: { onRetry: () => void }) {
  return (
    <StatusPage
      title="This page couldn't load"
      body="Something went wrong on our side. Nothing about your record or plan has changed. Try again in a moment."
      primary={{ label: "Try again", onClick: onRetry }}
      secondary={{ label: "Go home", href: "/home" }}
    />
  );
}
