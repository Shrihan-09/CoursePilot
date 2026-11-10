import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { SetupWizard } from "@/features/setup/setup-wizard";
import { requireAccount } from "@/lib/auth/require-account";
import { homeFor } from "@/lib/setup";

export const metadata: Metadata = { title: "Set up" };
export const dynamic = "force-dynamic";

/** Prompt 10a: the first-run setup wizard. Returning users go straight Home. */
export default async function OnboardingPage() {
  const { me } = await requireAccount("/onboarding");
  if (homeFor(me) === "/home") redirect("/home");
  return <SetupWizard />;
}
