"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Development status page.
 *
 * Deliberately not a product UI. It reads /meta and shows what the backend
 * actually supports, which makes "is the stack wired up correctly" a
 * one-glance question during early development.
 */
export default function Home() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["meta"],
    queryFn: api.meta,
  });

  return (
    <main className="mx-auto max-w-2xl p-8">
      <h1 className="text-2xl font-semibold">CoursePilot</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Rutgers degree planning &middot; foundation scaffold
      </p>

      <section className="mt-8 rounded-lg border p-4">
        <h2 className="text-sm font-medium">Backend connection</h2>

        {isLoading && <p className="mt-2 text-sm">Checking&hellip;</p>}

        {isError && (
          <p className="mt-2 text-sm text-red-600">
            Cannot reach the API. Is the backend running on port 8000?
          </p>
        )}

        {data && (
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <dt className="text-muted-foreground">Environment</dt>
            <dd>{data.environment}</dd>
            <dt className="text-muted-foreground">LLM provider</dt>
            <dd>{data.llm_provider}</dd>
            <dt className="text-muted-foreground">Retrieval</dt>
            <dd>{data.retrieval_strategy}</dd>
            <dt className="text-muted-foreground">Rutgers terms loaded</dt>
            <dd>{data.loaded_terms.length === 0 ? "none" : data.loaded_terms.join(", ")}</dd>
          </dl>
        )}
      </section>

      {data && (
        <section className="mt-4 rounded-lg border p-4">
          <h2 className="text-sm font-medium">Capabilities</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {Object.entries(data.capabilities).map(([name, enabled]) => (
              <li key={name} className="flex justify-between">
                <span>{name.replace(/_/g, " ")}</span>
                <span className={enabled ? "text-green-600" : "text-muted-foreground"}>
                  {enabled ? "available" : "not built yet"}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
