import { redirect } from "next/navigation";

import { AppShell } from "@/components/shell/app-shell";
import { displayIdentity } from "@/lib/auth/session";
import { requireAccount } from "@/lib/auth/require-account";
import { homeFor } from "@/lib/setup";

export const dynamic = "force-dynamic";

/** Signed-in product area. Students who haven't finished setup go to the setup wizard (Prompt 10a). */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const { session, me } = await requireAccount("/home");
  if (homeFor(me) === "/onboarding") redirect("/onboarding");
  return <AppShell identity={displayIdentity(session).email}>{children}</AppShell>;
}
