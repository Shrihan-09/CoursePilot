"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ThemeProvider } from "@/components/theme/theme-provider";

/**
 * React Query provider.
 *
 * The client is created inside `useState` rather than at module scope: in the
 * Next.js App Router, module-level state is shared across requests on the
 * server, which would leak one user's cached data into another's response.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            // Rutgers course data changes on a term cadence, not a
            // second-by-second one. Refetching on every window focus is
            // wasted traffic here.
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  // UI polish B4: the offline-page service worker (production builds only, so
  // it never caches anything under `next dev`).
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => { /* installability is optional */ });
  }, []);

  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ThemeProvider>
  );
}
