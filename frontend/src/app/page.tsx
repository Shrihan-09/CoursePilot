import { redirect } from "next/navigation";

import { currentSession } from "@/lib/server-api";

export const dynamic = "force-dynamic";

/** No splash, no artificial delay: straight to where the person belongs. */
export default async function Index() {
  redirect((await currentSession()) ? "/home" : "/signin");
}
