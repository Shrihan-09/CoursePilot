import { redirect } from "next/navigation";

import { AppShell } from "@/components/shell/app-shell";
import { displayIdentity } from "@/lib/auth/session";
import { currentSession, serverGet } from "@/lib/server-api";
import type { Me } from "@/types/onboarding";

export const dynamic = "force-dynamic";

/** Signed-in product area. Students without an academic profile go to onboarding. */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await currentSession();
  if (!session) redirect("/signin");
  const me = await serverGet<Me>("/me");
  if (me.status === 401) redirect("/auth/login?returnTo=/home");
  if (me.data && !me.data.has_academic_profile) redirect("/onboarding");
  return <AppShell identity={displayIdentity(session).email}>{children}</AppShell>;
}
