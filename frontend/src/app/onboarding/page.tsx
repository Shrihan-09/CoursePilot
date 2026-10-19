import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { OnboardingFlow } from "@/features/onboarding/onboarding-flow";
import { currentSession } from "@/lib/server-api";

export const metadata: Metadata = { title: "Set up" };
export const dynamic = "force-dynamic";

export default async function OnboardingPage() {
  if (!(await currentSession())) redirect("/signin?returnTo=/onboarding");
  return <OnboardingFlow />;
}
