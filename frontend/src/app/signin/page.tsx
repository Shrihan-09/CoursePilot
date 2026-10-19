import { KeyRound, ShieldCheck, Upload } from "lucide-react";
import type { Metadata } from "next";

import { Mark } from "@/components/brand/mark";
import { Button } from "@/components/ui/button";
import { Alert, Badge } from "@/components/ui/feedback";
import { authConfig, safeReturnTo } from "@/lib/auth/config";

export const metadata: Metadata = { title: "Sign in" };
export const dynamic = "force-dynamic";

const ERRORS: Record<string, string> = {
  unavailable: "Sign-in isn't available on this server right now.",
  provider: "We couldn't reach the sign-in provider. Please try again.",
  expired: "That sign-in took too long or was already used. Please start again.",
  failed: "Sign-in couldn't be completed. Please try again.",
  cancelled: "Sign-in was cancelled.",
  "dev-subject": "Use 1–64 letters, numbers, dots, dashes or @.",
};

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const config = authConfig();
  const returnTo = safeReturnTo(params.returnTo);
  const error = params.error ? ERRORS[params.error] ?? ERRORS.failed : null;

  return (
    <main className="flex min-h-dvh items-center justify-center bg-surface px-4 py-10">
      <div className="w-full max-w-[26rem] space-y-6">
        <div className="flex flex-col items-center gap-4 text-center">
          <Mark size={88} priority />
          <div className="space-y-1.5">
            <h1 className="text-2xl font-semibold tracking-tight text-fg">Welcome to CoursePilot</h1>
            <p className="text-sm text-fg-secondary">
              Plan your Rutgers degree with answers you can check.
            </p>
          </div>
        </div>

        <div className="space-y-4 rounded-lg border border-border bg-surface-elevated p-5 shadow-elevated sm:p-6">
          {params.signed_out && <Alert tone="success">You're signed out of CoursePilot.</Alert>}
          {error && <Alert tone="danger">{error}</Alert>}

          {config.mode === "oidc" && (
            <form action="/auth/login" method="get">
              <input type="hidden" name="returnTo" value={returnTo} />
              <Button type="submit" variant="primary" size="lg" className="w-full">
                {config.signInLabel}
              </Button>
            </form>
          )}

          {config.mode === "dev" && (
            <form action="/auth/dev" method="post" className="space-y-3">
              <div className="flex items-center justify-between">
                <label htmlFor="subject" className="text-sm font-medium text-fg">
                  Development sign-in
                </label>
                <Badge tone="warning">Local only</Badge>
              </div>
              <input type="hidden" name="returnTo" value={returnTo} />
              <input
                id="subject"
                name="subject"
                required
                maxLength={64}
                defaultValue="student-1"
                autoComplete="off"
                className="h-10 w-full rounded border border-border-strong bg-surface-elevated px-3 text-sm"
              />
              <Button type="submit" variant="primary" size="lg" className="w-full">
                {config.signInLabel}
              </Button>
              <p className="text-xs text-fg-muted">
                Not authentication: any name signs in. Refused in production.
              </p>
            </form>
          )}

          {config.mode === "disabled" && (
            <Alert tone="warning" title="Sign-in isn't configured">
              This server has no identity provider set up, so no one can sign in.
            </Alert>
          )}

          <ul className="space-y-3 border-t border-border pt-4 text-sm text-fg-secondary">
            <li className="flex gap-3">
              <KeyRound aria-hidden className="mt-0.5 size-4 shrink-0 text-fg-muted" />
              <span>
                Signing in only tells CoursePilot who you are. We never ask for your Rutgers
                password and can't see your Rutgers records, email or files.
              </span>
            </li>
            <li className="flex gap-3">
              <Upload aria-hidden className="mt-0.5 size-4 shrink-0 text-fg-muted" />
              <span>
                Your courses come only from a transcript you upload or courses you enter - and
                nothing is saved until you review and confirm it.
              </span>
            </li>
            <li className="flex gap-3">
              <ShieldCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-fg-muted" />
              <span>
                CoursePilot is a planning aid, not an official Rutgers service. Your advisor and
                Rutgers' records are authoritative.
              </span>
            </li>
          </ul>
        </div>
      </div>
    </main>
  );
}
