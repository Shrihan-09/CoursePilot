import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { SetupWizard } from "@/features/setup/setup-wizard";
import { currentSession, serverGet } from "@/lib/server-api";
import { homeFor } from "@/lib/setup";
import type { Me } from "@/types/onboarding";

export const metadata: Metadata = { title: "Set up" };
export const dynamic = "force-dynamic";

/** Prompt 10a: the first-run setup wizard. Returning users go straight Home. */
export default async function OnboardingPage() {
  if (!(await currentSession())) redirect("/signin?returnTo=/onboarding");
  const me = await serverGet<Me>("/me");
  if (me.status === 401) redirect("/auth/login?returnTo=/onboarding");
  if (homeFor(me.data) === "/home" && me.data) redirect("/home");
  return <SetupWizard />;
}
