"use client";

import { useEffect, useState } from "react";

import { GenerationAnimation, type GenerationStage } from "@/components/brand/generation";
import { Mark, Wordmark } from "@/components/brand/mark";
import { Button } from "@/components/ui/button";
import {
  Alert,
  EmptyState,
  ErrorState,
  PageHeader,
  Progress,
  Skeleton,
  StatusBadge,
} from "@/components/ui/feedback";
import { Checkbox, Field, Input, Radio, SearchInput, Select } from "@/components/ui/field";
import { Dialog, Tabs, Tooltip } from "@/components/ui/overlay";
import { ROW_STATE } from "@/features/onboarding/labels";
import type { RowState } from "@/types/onboarding";

/**
 * Design-system and motion QA surface (development only). The generation
 * demo SIMULATES stages on a timer to show the motion; in the product the
 * stages come from the backend.
 */
const DEMO_STAGES = ["Reading your record", "Checking prerequisites", "Placing courses by term",
                     "Checking requirement coverage"];

function useDemoStages(cycleMs = 1400) {
  const [step, setStep] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setStep((s) => (s + 1) % (DEMO_STAGES.length + 2)), cycleMs);
    return () => window.clearInterval(id);
  }, [cycleMs]);
  const stages: GenerationStage[] = DEMO_STAGES.map((label, i) => ({
    key: label, label, state: i < step ? "done" : i === step ? "active" : "pending",
  }));
  return { stages, complete: step >= DEMO_STAGES.length };
}

const TOKENS = ["background", "surface", "surface-elevated", "surface-hover", "border", "border-strong",
                "primary", "primary-hover", "primary-subtle", "success", "warning", "danger"];

function Panel({ theme, title, children }: { theme: "light" | "dark"; title: string; children: React.ReactNode }) {
  return (
    <div className={`theme-${theme} rounded-lg border border-border bg-background p-4 text-fg`}>
      <p className="mb-3 text-xs font-medium uppercase tracking-wide text-fg-muted">{title}</p>
      {children}
    </div>
  );
}

export function DesignGallery() {
  const demo = useDemoStages();
  const [dialog, setDialog] = useState(false);
  const [tab, setTab] = useState<"a" | "b" | "c">("a");

  return (
    <main className="mx-auto max-w-6xl space-y-10 px-4 py-8 sm:px-6">
      <PageHeader eyebrow="Phase 7.1" title="CoursePilot design system"
                  description="Tokens, primitives and the motion language. Development only." />

      <section className="space-y-3">
        <h2 className="text-sm font-semibold">Brand</h2>
        <div className="flex flex-wrap items-end gap-6">
          <Mark size={128} />
          <Mark size={64} />
          <Mark size={32} />
          <Mark size={16} />
          <Wordmark />
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        {(["light", "dark"] as const).map((theme) => (
          <Panel key={theme} theme={theme} title={`${theme} tokens`}>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
              {TOKENS.map((t) => (
                <div key={t} className="space-y-1">
                  <div className="h-10 rounded border border-border" style={{ background: `rgb(var(--${t}))` }} />
                  <p className="code text-2xs text-fg-muted">{t}</p>
                </div>
              ))}
            </div>
            <div className="mt-4 space-y-1">
              <p className="text-fg">Primary text - CoursePilot</p>
              <p className="text-fg-secondary">Secondary text</p>
              <p className="text-fg-muted">Muted text</p>
              <p className="text-primary-text underline">A link in CoursePilot blue</p>
            </div>
          </Panel>
        ))}
      </section>

      <section className="space-y-4">
        <h2 className="text-sm font-semibold">Generation animation</h2>
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel theme="light" title="Light">
            <GenerationAnimation status={demo.complete ? "complete" : "generating"} stages={demo.stages} />
          </Panel>
          <Panel theme="dark" title="Dark">
            <GenerationAnimation status={demo.complete ? "complete" : "generating"} stages={demo.stages} />
          </Panel>
          <Panel theme="light" title="Reduced motion (forced)">
            <GenerationAnimation status="generating" stages={demo.stages} reducedMotion />
          </Panel>
          <Panel theme="dark" title="Compact (narrow container)">
            <div className="mx-auto max-w-[220px] [&_.cp-gen]:[--cp-gen-size:7.5rem]">
              <GenerationAnimation status="generating" stages={[]} title="Building your schedule"
                                   caption="Deterministic schedule engine." />
            </div>
          </Panel>
        </div>
        <p className="text-xs text-fg-muted">Demo stages are simulated on a timer here; real callers pass backend stages.</p>
      </section>

      <section className="space-y-4">
        <h2 className="text-sm font-semibold">Transcript row states (icon + words, never colour alone)</h2>
        <div className="flex flex-wrap gap-2">
          {(Object.keys(ROW_STATE) as RowState[]).map((s) => {
            const st = ROW_STATE[s];
            return <StatusBadge key={s} tone={st.tone} icon={st.icon} label={st.label} />;
          })}
        </div>
      </section>

      <section className="grid gap-6 md:grid-cols-2">
        <div className="space-y-3">
          <h2 className="text-sm font-semibold">Actions</h2>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary">Primary</Button>
            <Button>Secondary</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger">Danger</Button>
            <Button variant="primary" loading>Saving</Button>
            <Tooltip content="Explains a control on hover and focus"><Button>Tooltip</Button></Tooltip>
            <Button onClick={() => setDialog(true)}>Open dialog</Button>
          </div>
          <Tabs label="Example" value={tab} onChange={setTab}
                items={[{ value: "a", label: "Needs you", count: 3 }, { value: "b", label: "All", count: 16 },
                        { value: "c", label: "Left out", count: 0 }]} />
          <Progress value={62} label="Example progress" />
          <Progress value={null} label="Indeterminate" />
        </div>
        <div className="space-y-3">
          <h2 className="text-sm font-semibold">Inputs</h2>
          <Field label="Course code" hint="e.g. 01:198:111">
            {({ id, describedBy }) => <Input id={id} aria-describedby={describedBy} className="code" placeholder="01:198:111" />}
          </Field>
          <Field label="Grade" error="Not a Rutgers grade symbol.">
            {({ id, describedBy, invalid }) => <Input id={id} aria-describedby={describedBy} aria-invalid={invalid} defaultValue="A-" />}
          </Field>
          <Select aria-label="Term"><option>Fall 2026</option><option>Spring 2027</option></Select>
          <SearchInput placeholder="Search programs" aria-label="Search" />
          <Checkbox label="Include summer terms" description="Only when you plan to take summer courses." />
          <Radio name="r" label="Completed" defaultChecked />
          <Radio name="r" label="In progress" />
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <div className="space-y-3">
          <Alert tone="info" title="Information">Calm, informative.</Alert>
          <Alert tone="success">Your transcript was saved.</Alert>
          <Alert tone="warning" title="Requirements pending review">Serious but not alarming.</Alert>
          <Alert tone="danger" title="This transcript couldn't be used">This PDF is a scan.</Alert>
        </div>
        <div className="space-y-3">
          <Skeleton className="h-6 w-1/2" />
          <Skeleton className="h-14" />
          <EmptyState title="No courses yet">Courses you add appear here.</EmptyState>
          <ErrorState requestId="3f2a9c1d7e" onRetry={() => undefined}>Programs couldn&apos;t be loaded.</ErrorState>
        </div>
      </section>

      <Dialog open={dialog} onClose={() => setDialog(false)} title="Discard this upload?"
              description="The extracted rows are deleted. Your record is not changed."
              footer={<><Button onClick={() => setDialog(false)}>Cancel</Button>
                        <Button variant="danger" onClick={() => setDialog(false)}>Discard</Button></>} />
    </main>
  );
}
