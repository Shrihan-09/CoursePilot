import { Skeleton } from "@/components/ui/feedback";

/** Shown while a signed-in page's server work runs: never a blank screen. */
export default function Loading() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading">
      <Skeleton className="h-10 w-64" />
      <div className="grid gap-4 md:grid-cols-2">
        <Skeleton className="h-48" />
        <Skeleton className="h-48" />
      </div>
      <Skeleton className="h-32" />
    </div>
  );
}
