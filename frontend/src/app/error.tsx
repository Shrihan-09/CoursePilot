"use client";

import { PageError } from "@/components/feedback/page-error";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <PageError onRetry={() => { reset(); window.location.reload(); }} />;
}
