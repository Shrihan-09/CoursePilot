"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Clock3, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Wordmark } from "@/components/brand/mark";
import { GenerationAnimation, type GenerationStage } from "@/components/brand/generation";
import { Button } from "@/components/ui/button";
import { Alert, ErrorState, Skeleton } from "@/components/ui/feedback";
import { ToastProvider } from "@/components/ui/overlay";
import { useDnProgress } from "@/features/degree/degree-navigator";
import { ApiError, api } from "@/lib/api";
import {
  SETUP_STEPS,
  STEP_KEYS,
  asProfileInput,
  planSourceLabel,
  resumeStep,
  stepCounter,
  stepProgress,
  withStepDone,
  type SetupStepKey,
} from "@/lib/setup";
import { cn } from "@/lib/utils";
import type { PlanningProfileResponse } from "@/types/personalization";

import {
  AboutStep,
  IntensityStep,
  MustsStep,
  RecordStep,
  ScheduleStep,
  WeekStep,
  initialAbout,
  type AboutAnswers,
  type Draft,
} from "./steps";

/**
 * Prompt 10a first-run setup wizard. One topic per screen; every answer is
 * saved as the student leaves the step (the same profile writes Planning
 * preferences makes - no new planning logic), and the step list itself is
 * saved so a student who leaves resumes at the first unfinished step.
 */
export function SetupWizard() {
  const router = useRouter();
  const client = useQueryClient();
  const status = useQuery({ queryKey: ["onboarding"], queryFn: api.onboarding });
  const hasProfile = Boolean(status.data?.profile);
  const meta = useQuery({ queryKey: ["planning-profile"], queryFn: api.planningProfile, enabled: hasProfile });
  const dn = useDnProgress();

  const [step, setStep] = useState<SetupStepKey | null>(null);
  const [done, setDone] = useState<SetupStepKey[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [about, setAbout] = useState<AboutAnswers | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [building, setBuilding] = useState<"generating" | "complete" | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const firstFocus = useRef(true);

  // Resume where the saved progress says, once.
  useEffect(() => {
    if (!status.data || step !== null) return;
    setStep(resumeStep(status.data));
    setDone((status.data.setup?.completed_steps ?? []) as SetupStepKey[]);
  }, [status.data, step]);
  useEffect(() => {
    if (meta.data && draft === null) setDraft(asProfileInput(meta.data.profile ?? meta.data.defaults));
  }, [meta.data, draft]);
  useEffect(() => {
    if (status.data && about === null && (!hasProfile || meta.data)) {
      setAbout(initialAbout(status.data.profile, meta.data ? asProfileInput(meta.data.profile ?? meta.data.defaults) : null));
    }
  }, [status.data, meta.data, hasProfile, about]);
  // Focus moves to the step heading on every step change (not on first paint).
  useEffect(() => {
    if (!step) return;
    if (firstFocus.current) { firstFocus.current = false; return; }
    heading.current?.focus();
  }, [step]);

  if (status.isPending) {
    return <Frame><div className="space-y-3"><Skeleton className="h-8 w-2/3" /><Skeleton className="h-48" /></div></Frame>;
  }
  if (status.isError) {
    return <Frame><ErrorState onRetry={() => status.refetch()}>Your setup couldn&apos;t be loaded.</ErrorState></Frame>;
  }
  if (!step) return <Frame><Skeleton className="h-48" /></Frame>;

  const info = SETUP_STEPS.find((s) => s.key === step)!;
  const index = STEP_KEYS.indexOf(step);
  const prev = index > 0 ? STEP_KEYS[index - 1]! : null;
  const next = STEP_KEYS[index + 1] ?? null;

  const message = (e: unknown) =>
    e instanceof ApiError && e.status < 500 ? (e.issues[0]?.message ?? e.message) : "That didn't save. Please try again.";

  /** Save this step (unless skipped), record it as done, and move on. */
  async function leave(save: (() => Promise<string | null | undefined | void>) | null) {
    setBusy(true);
    setError(null);
    try {
      let firstTerm: string | null | undefined;
      if (save) firstTerm = (await save()) ?? undefined;
      else if (meta.data) setDraft(asProfileInput(meta.data.profile ?? meta.data.defaults)); // skipped: drop unsaved edits
      let list = withStepDone(done, step!);
      if (step === "about") list = withStepDone(list, "welcome");
      await api.saveSetup(list, step === "about" ? (firstTerm ?? about?.firstTerm ?? null) : undefined);
      setDone(list);
      await client.invalidateQueries({ queryKey: ["onboarding"] });
      if (next) setStep(next);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  const saveDraft = async () => {
    if (!draft) return;
    const saved = await api.confirmPlanningProfile(asProfileInput(draft));
    setDraft(asProfileInput(saved));
    await client.invalidateQueries({ queryKey: ["planning-profile"] });
  };

  const saveAbout = async () => {
    const a = about!;
    const profile = status.data!.profile;
    if (!profile) await api.createProfile(a.programKey, a.catalogYear);
    else if (profile.program.program_key !== a.programKey || profile.program.catalog_year !== a.catalogYear) {
      await api.changeProgram(a.programKey, a.catalogYear);
    }
    if ((profile?.declaration_status ?? null) !== a.declaration) await api.setDeclaration(a.declaration);
    const m = await client.fetchQuery({ queryKey: ["planning-profile"], queryFn: api.planningProfile, staleTime: 0 });
    const base = asProfileInput(draft ?? m.profile ?? m.defaults);
    const saved = await api.confirmPlanningProfile({ ...base, pace: a.pace, expected_graduation_term: a.gradTerm });
    setDraft(asProfileInput(saved));
    await client.invalidateQueries({ queryKey: ["profile"] });
    return a.firstTerm;
  };

  async function build() {
    setBusy(true);
    setError(null);
    setBuilding("generating");
    try {
      const m: PlanningProfileResponse = meta.data ?? await api.planningProfile();
      if (!m.profile) await api.confirmPlanningProfile(asProfileInput(draft ?? m.defaults));
      await api.completeOnboarding();
      try {
        await api.savePlan();
      } catch {
        // The plan page explains what is missing; setup itself is done.
      }
      await api.saveSetup(STEP_KEYS);
      await Promise.all(["onboarding", "plan", "readiness", "planning-profile", "profile"].map(
        (key) => client.invalidateQueries({ queryKey: [key] })));
      setBuilding("complete");
    } catch (e) {
      setBuilding(null);
      setError(e instanceof ApiError && e.code !== "internal_error" && e.status < 500
        ? `${message(e)} Go back to "Bring your record" to finish or discard it.`
        : "Your plan couldn't be built. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const aboutInvalid = step === "about" && (!about?.programKey || !about.catalogYear);
  const needsMeta = ["intensity", "week", "schedule", "musts"].includes(step);
  const metaReady = Boolean(meta.data && draft);

  if (building) {
    return (
      <Frame>
        <GenerationAnimation
          status={building}
          stages={buildStages(building)}
          onResolved={() => { router.replace("/home"); router.refresh(); }}
        />
      </Frame>
    );
  }

  return (
    <Frame>
      {step !== "welcome" && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs font-medium text-fg-muted">
            <span>{stepCounter(step)}</span>
            <span>{info.title}</span>
          </div>
          <div role="progressbar" aria-label="Setup progress" aria-valuemin={0} aria-valuemax={100}
            aria-valuenow={stepProgress(step)} aria-valuetext={stepCounter(step)}
            className="h-1.5 overflow-hidden rounded-full bg-surface-hover">
            <div className="cp-glow-fill h-full rounded-full" style={{ width: `${stepProgress(step)}%` }} />
          </div>
        </div>
      )}

      <section key={step} aria-labelledby="setup-step-title"
        className="cp-glow-card animate-cp-slide-up space-y-6 rounded-xl border border-border bg-surface-elevated p-5 sm:p-8">
        <header className="space-y-1.5">
          <h1 id="setup-step-title" ref={heading} tabIndex={-1} className="text-2xl font-semibold tracking-tight text-fg outline-none sm:text-3xl">
            {step === "welcome" ? "Welcome to CoursePilot" : info.title}
          </h1>
          {info.why && <p className="text-sm text-fg-secondary">{info.why}</p>}
        </header>

        {step === "welcome" && (
          <div className="space-y-4">
            <p className="text-base text-fg-secondary">
              CoursePilot lays out the semesters that finish your degree, checked against Rutgers&apos; own requirements.
            </p>
            <p className="flex items-center gap-2 text-sm text-fg-muted"><Clock3 aria-hidden className="size-4" /> Takes about 3 minutes. You can stop and come back.</p>
          </div>
        )}
        {step === "about" && (about ? <AboutStep value={about} onChange={setAbout} /> : <Skeleton className="h-64" />)}
        {step === "record" && <RecordStep />}
        {needsMeta && !metaReady && (meta.isError
          ? <Alert tone="danger">Your planning settings couldn&apos;t be loaded. You can skip this step and set it later.</Alert>
          : <Skeleton className="h-48" />)}
        {step === "intensity" && metaReady && <IntensityStep meta={meta.data!} draft={draft!} setDraft={setDraft} />}
        {step === "week" && metaReady && <WeekStep meta={meta.data!} draft={draft!} setDraft={setDraft} />}
        {step === "schedule" && metaReady && <ScheduleStep meta={meta.data!} draft={draft!} setDraft={setDraft} />}
        {step === "musts" && metaReady && <MustsStep draft={draft!} setDraft={setDraft} />}
        {step === "build" && (
          <div className="space-y-4">
            <p className="text-base text-fg-secondary">
              CoursePilot will lay out your semesters from what you&apos;ve told it. Everything stays editable under Plan → Preferences.
            </p>
            <p className="text-sm font-medium text-fg">{planSourceLabel(dn.data)}</p>
          </div>
        )}

        {error && <Alert tone="danger">{error}</Alert>}

        <footer className="flex flex-col-reverse gap-3 pt-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            {prev && step !== "about" && (
              <Button variant="ghost" onClick={() => { setError(null); setStep(prev); }} disabled={busy}>
                <ArrowLeft aria-hidden className="size-4" /> Back
              </Button>
            )}
            {step === "about" && !hasProfile && (
              <Button variant="ghost" onClick={() => { setError(null); setStep("welcome"); }} disabled={busy}>
                <ArrowLeft aria-hidden className="size-4" /> Back
              </Button>
            )}
          </div>
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            {info.skippable && (
              <Button variant="secondary" size="lg" onClick={() => leave(null)} disabled={busy}>Skip for now</Button>
            )}
            {step === "welcome" && (
              <Button variant="primary" size="lg" onClick={() => setStep("about")}>Start <ArrowRight aria-hidden className="size-4" /></Button>
            )}
            {step === "about" && (
              <Button variant="primary" size="lg" loading={busy} disabled={aboutInvalid || !about} onClick={() => leave(saveAbout)}>
                Next <ArrowRight aria-hidden className="size-4" />
              </Button>
            )}
            {step === "record" && (
              <Button variant="primary" size="lg" loading={busy} onClick={() => leave(async () => undefined)}>
                Next <ArrowRight aria-hidden className="size-4" />
              </Button>
            )}
            {needsMeta && (
              <Button variant="primary" size="lg" loading={busy} disabled={!metaReady} onClick={() => leave(saveDraft)}>
                Next <ArrowRight aria-hidden className="size-4" />
              </Button>
            )}
            {step === "build" && (
              <Button variant="primary" size="lg" loading={busy} onClick={build}>
                <Sparkles aria-hidden className="size-4" /> Build my plan
              </Button>
            )}
          </div>
        </footer>
        {aboutInvalid && <p className="text-xs text-fg-muted">Choose your major to continue.</p>}
      </section>
    </Frame>
  );
}

function buildStages(state: "generating" | "complete"): GenerationStage[] {
  const s = state === "complete" ? "done" : "active";
  return [
    { key: "prefs", label: "Saving your answers", state: "done" },
    { key: "plan", label: "Laying out your semesters", state: s },
  ];
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <ToastProvider>
      <div className="min-h-dvh bg-canvas">
        <header className="sticky top-0 z-20 border-b border-border bg-background/90 backdrop-blur">
          <div className="mx-auto flex h-14 max-w-2xl items-center justify-between gap-4 px-4 sm:px-6">
            <Wordmark size={26} />
            <form action="/auth/logout" method="post">
              <Button type="submit" variant="ghost" size="sm">Sign out</Button>
            </form>
          </div>
        </header>
        <main className={cn("mx-auto max-w-2xl space-y-5 px-4 pb-24 pt-6 sm:px-6 sm:pt-10")}>{children}</main>
      </div>
    </ToastProvider>
  );
}
