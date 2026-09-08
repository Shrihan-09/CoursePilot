"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

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

  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
