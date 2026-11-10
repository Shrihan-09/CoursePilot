import "server-only";

import { redirect } from "next/navigation";

import { currentSession, serverGet } from "@/lib/server-api";
import type { Me } from "@/types/onboarding";
import { safeReturnTo } from "./config";

/** Only a successful API check can decide setup vs app navigation. */
export async function requireAccount(returnTo: string) {
  const target = encodeURIComponent(safeReturnTo(returnTo));
  const session = await currentSession();
  if (!session) redirect(`/signin?returnTo=${target}`);
  const me = await serverGet<Me>("/me");
  if (me.status === 401) redirect(`/auth/session-error?returnTo=${target}`);
  if (me.status !== 200 || !me.data) {
    // Includes 403, missing schema, network failures and timeouts. Do not
    // mistake an unavailable account check for an unfinished setup wizard.
    throw new Error("CoursePilot couldn't check your account. Please try again.");
  }
  return { session, me: me.data };
}
