import { redirect } from "next/navigation";

import { AppShell } from "@/components/shell/app-shell";
import { displayIdentity } from "@/lib/auth/session";
import { currentSession, serverGet } from "@/lib/server-api";
import { homeFor } from "@/lib/setup";
import type { Me } from "@/types/onboarding";

export const dynamic = "force-dynamic";

/** Signed-in product area. Students who haven't finished setup go to the setup wizard (Prompt 10a). */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await currentSession();
  if (!session) redirect("/signin");
  const me = await serverGet<Me>("/me");
  if (me.status === 401) redirect("/auth/login?returnTo=/home");
  if (homeFor(me.data) === "/onboarding") redirect("/onboarding");
  return <AppShell identity={displayIdentity(session).email}>{children}</AppShell>;
}
