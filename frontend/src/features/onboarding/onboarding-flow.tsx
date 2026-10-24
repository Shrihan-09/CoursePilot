"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, FileText, PencilLine, Sparkle } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Wordmark } from "@/components/brand/mark";
import { Button } from "@/components/ui/button";
import { Alert, ErrorState, Skeleton } from "@/components/ui/feedback";
import { ToastProvider } from "@/components/ui/overlay";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Onboarding } from "@/types/onboarding";

import { ManualEntry } from "./manual-entry";
import { PlacementStep } from "./placement-step";
import { ProgramStep } from "./program-step";
import { TranscriptReview, TranscriptUpload } from "./transcript";
import { WorkloadStep } from "./workload-step";

type View = "welcome" | "program" | "history" | "transcript" | "review" | "manual" | "placement" | "workload" | "done";

/**
 * Welcome -> program -> transcript OR manual -> review -> confirm -> (optional) math
 * placement -> (optional) workload -> complete.
 * The server's onboarding state decides where a returning student resumes;
 * this component only navigates between the steps the API allows.
 */
export function OnboardingFlow() {
  const router = useRouter();
  const client = useQueryClient();
  const status = useQuery({ queryKey: ["onboarding"], queryFn: api.onboarding });
  const [view, setView] = useState<View | null>(null);
  const [importId, setImportId] = useState<string | null>(null);

  // Resume where the server says the student is.
  useEffect(() => {
    if (!status.data || view !== null) return;
    setView(initialView(status.data));
    const open = status.data.open_imports.find((i) => i.state === "extracted");
    if (open) setImportId(open.import_id);
  }, [status.data, view]);

  const complete = useMutation({
    mutationFn: api.completeOnboarding,
    onSuccess: () => {
      router.replace("/home");
      router.refresh();
    },
  });
  const refresh = () => client.invalidateQueries({ queryKey: ["onboarding"] });

  const steps: { key: View[]; label: string }[] = [
    { key: ["welcome", "program"], label: "Program" },
    { key: ["history", "transcript", "manual"], label: "Courses" },
    { key: ["review"], label: "Review" },
    { key: ["placement"], label: "Placement" },
    { key: ["workload"], label: "Workload" },
    { key: ["done"], label: "Finish" },
  ];
  const stepIndex = view ? steps.findIndex((s) => s.key.includes(view)) : 0;

  return (
    <ToastProvider>
      <div className="min-h-dvh bg-surface">
        <header className="sticky top-0 z-20 border-b border-border bg-background/90 backdrop-blur">
          <div className="mx-auto flex h-14 max-w-3xl items-center justify-between gap-4 px-4 sm:px-6">
            <Wordmark size={26} />
            <form action="/auth/logout" method="post">
              <Button type="submit" variant="ghost" size="sm">Sign out</Button>
            </form>
          </div>
        </header>

        <main className="mx-auto max-w-3xl space-y-6 px-4 pb-32 pt-6 sm:px-6 sm:pt-10">
          {view && view !== "welcome" && (
            <ol aria-label="Setup steps" className="flex flex-wrap items-center gap-1.5 text-xs sm:gap-2">
              {steps.map((s, i) => (
                <li key={s.label} className="flex items-center gap-2">
                  <span
                    aria-current={i === stepIndex ? "step" : undefined}
                    className={cn(
                      "flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium",
                      i < stepIndex && "text-fg-secondary",
                      i === stepIndex && "bg-primary-subtle text-primary-text",
                      i > stepIndex && "text-fg-muted",
                    )}
                  >
                    {i < stepIndex ? <Check aria-hidden className="size-3.5" /> : <span className="tabular">{i + 1}</span>}
                    <span className={cn(i !== stepIndex && "sr-only sm:not-sr-only")}>{s.label}</span>
                  </span>
                  {i < steps.length - 1 && <span aria-hidden className="h-px w-3 bg-border-strong sm:w-6" />}
                </li>
              ))}
            </ol>
          )}

          {status.isPending && (
            <div className="space-y-3">
              <Skeleton className="h-8 w-2/3" />
              <Skeleton className="h-40" />
            </div>
          )}
          {status.isError && (
            <ErrorState onRetry={() => status.refetch()}>Your setup status couldn&apos;t be loaded.</ErrorState>
          )}

          {view === "welcome" && (
            <section className="space-y-6 py-4">
              <div className="space-y-2">
                <h1 className="text-3xl font-semibold tracking-tight text-fg">Let&apos;s set up CoursePilot</h1>
                <p className="max-w-xl text-fg-secondary">
                  Two steps: choose your program, then add the courses you&apos;ve taken - from your
                  unofficial transcript or by hand. You&apos;ll review everything before it&apos;s saved.
                </p>
              </div>
              <Button variant="primary" size="lg" onClick={() => setView("program")}>
                Get started <ArrowRight aria-hidden className="size-4" />
              </Button>
            </section>
          )}

          {view === "program" && (
            <ProgramStep onCreated={async () => {
              await refresh();
              setView("history");
            }} />
          )}

          {view === "history" && (
            <section aria-labelledby="history-title" className="space-y-5">
              <div className="space-y-1">
                <h2 id="history-title" className="text-xl font-semibold tracking-tight text-fg">
                  Add the courses you&apos;ve taken
                </h2>
                <p className="text-sm text-fg-secondary">
                  CoursePilot uses only what you provide and confirm.
                </p>
              </div>
              {status.data?.profile?.program.warnings.map((w) => (
                <Alert key={w.code} tone={w.severity === "warning" ? "warning" : "info"}>{w.message}</Alert>
              ))}
              <div className="grid gap-3 sm:grid-cols-2">
                <ChoiceCard icon={FileText} title="Upload unofficial transcript"
                            body="Fastest. CoursePilot reads the PDF; you review every course."
                            recommended onClick={() => setView("transcript")} />
                <ChoiceCard icon={PencilLine} title="Enter courses yourself"
                            body="Add each course with its term and grade."
                            onClick={() => setView("manual")} />
              </div>
              <Button variant="link" onClick={() => setView("placement")}>
                I haven&apos;t taken any courses yet
              </Button>
            </section>
          )}

          {view === "transcript" && (
            <section className="space-y-5">
              <div className="space-y-1">
                <h2 className="text-xl font-semibold tracking-tight text-fg">Upload your unofficial transcript</h2>
                <p className="text-sm text-fg-secondary">
                  Only the PDF you choose is read. CoursePilot never signs in to Rutgers for you.
                </p>
              </div>
              <TranscriptUpload onUploaded={(id) => {
                setImportId(id);
                setView("review");
              }} />
              <Button variant="ghost" onClick={() => setView("history")}>Back</Button>
            </section>
          )}

          {view === "review" && importId && (
            <TranscriptReview
              importId={importId}
              onConfirmed={async () => {
                await refresh();
                setView("placement");
              }}
              onDiscarded={async () => {
                setImportId(null);
                await refresh();
                setView("history");
              }}
            />
          )}

          {view === "manual" && (
            <section className="space-y-5">
              <div className="space-y-1">
                <h2 className="text-xl font-semibold tracking-tight text-fg">Enter your courses</h2>
                <p className="text-sm text-fg-secondary">Add as many as you like - you can change them later.</p>
              </div>
              <ManualEntry />
              <div className="flex justify-between">
                <Button variant="ghost" onClick={() => setView("history")}>Back</Button>
                <Button variant="primary" onClick={() => setView("placement")}>
                  Done adding <ArrowRight aria-hidden className="size-4" />
                </Button>
              </div>
            </section>
          )}

          {view === "placement" && (
            <PlacementStep initial={status.data?.profile?.math_placement} onDone={async () => {
              await refresh();
              setView("workload");
            }} />
          )}

          {view === "workload" && (
            <WorkloadStep onDone={() => setView("done")} />
          )}

          {view === "done" && (
            <section className="space-y-5 py-4">
              <div className="flex size-11 items-center justify-center rounded-full bg-success-subtle">
                <Check aria-hidden className="size-5 text-success" />
              </div>
              <div className="space-y-1">
                <h2 className="text-2xl font-semibold tracking-tight text-fg">You&apos;re set up</h2>
                <p className="text-sm text-fg-secondary">
                  {summary(status.data)} You can add or correct courses at any time.
                </p>
              </div>
              {complete.isError && <Alert tone="danger">That didn&apos;t go through. Please try again.</Alert>}
              <Button variant="primary" size="lg" loading={complete.isPending} onClick={() => complete.mutate()}>
                Go to CoursePilot <ArrowRight aria-hidden className="size-4" />
              </Button>
            </section>
          )}
        </main>
      </div>
    </ToastProvider>
  );
}

function initialView(s: Onboarding): View {
  switch (s.state) {
    case "needs_profile": return "welcome";
    case "reviewing_transcript": return "review";
    case "needs_academic_history": return "history";
    default: return "done";
  }
}

function summary(s: Onboarding | undefined): string {
  if (!s) return "";
  const c = s.history_counts;
  const total = (c.completed ?? 0) + (c.in_progress ?? 0) + (c.planned ?? 0);
  if (total === 0) return "No courses on your record yet.";
  return `${total} course${total === 1 ? "" : "s"} on your record.`;
}

function ChoiceCard({
  icon: Icon,
  title,
  body,
  recommended,
  onClick,
}: {
  icon: typeof Sparkle;
  title: string;
  body: string;
  recommended?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex h-full flex-col gap-3 rounded-lg border border-border bg-surface-elevated p-4 text-left transition-colors hover:border-primary/60 hover:bg-surface-hover"
    >
      <span className="flex items-center justify-between">
        <Icon aria-hidden className="size-5 text-primary" />
        {recommended && <span className="text-2xs font-medium uppercase tracking-wide text-fg-muted">Recommended</span>}
      </span>
      <span className="space-y-1">
        <span className="block text-sm font-semibold text-fg">{title}</span>
        <span className="block text-sm text-fg-secondary">{body}</span>
      </span>
    </button>
  );
}
