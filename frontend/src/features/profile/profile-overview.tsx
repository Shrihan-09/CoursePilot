"use client";

import { useQuery } from "@tanstack/react-query";
import { Download, Eye, EyeOff, FileText, Lock, Settings2, Smartphone } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { api } from "@/lib/api";
import { termLabel } from "@/lib/academic/vocabulary";
import { useGpaVisible } from "@/lib/gpa-visibility";
import type { ClassStanding } from "@/types/onboarding";

/**
 * Profile (UI polish B3): who the student is in CoursePilot - program, class
 * year, catalog year, uploads with dates, preferences - plus the GPA, hidden
 * by default and revealed per device, and the app install button.
 */

const STANDING: Record<ClassStanding, string> = {
  first_year: "First-year", sophomore: "Sophomore", junior: "Junior", senior: "Senior", transfer: "Transfer",
};

const day = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : null;

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 py-2.5 sm:flex-row sm:items-baseline sm:gap-4">
      <dt className="w-36 shrink-0 text-xs font-medium uppercase tracking-wide text-fg-muted">{label}</dt>
      <dd className="min-w-0 text-sm text-fg [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

const card = "rounded-xl border border-border bg-surface-elevated p-4 sm:p-5";

export function GpaRow({ value }: { value: string | null }) {
  const [visible, setVisible] = useGpaVisible();
  if (!value) return <Row label="GPA">Not on file. Upload your unofficial transcript to add it.</Row>;
  return (
    <Row label="GPA">
      <span className="inline-flex flex-wrap items-center gap-2">
        {visible ? (
          <span className="tabular font-semibold" data-testid="gpa-value">{value}</span>
        ) : (
          <span className="tabular tracking-widest text-fg-muted" aria-label="GPA hidden">&bull;&bull;&bull;&bull;</span>
        )}
        <button type="button" aria-pressed={visible} onClick={() => setVisible(!visible)}
          className="inline-flex min-h-9 items-center gap-1.5 rounded-md border border-border px-2.5 text-xs font-medium text-fg-secondary hover:bg-surface-hover">
          {visible ? <EyeOff aria-hidden className="size-4" /> : <Eye aria-hidden className="size-4" />}
          {visible ? "Hide GPA" : "Show GPA"}
        </button>
      </span>
      <span className="mt-1 block text-xs text-fg-muted">
        As stated on the transcript you uploaded, not calculated by CoursePilot. Shown only on this device when you choose; never on Home or in shared views.
      </span>
    </Row>
  );
}

interface InstallEvent extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> }

export function InstallApp() {
  const [event, setEvent] = useState<InstallEvent | null>(null);
  const [installed, setInstalled] = useState(false);
  useEffect(() => {
    setInstalled(window.matchMedia("(display-mode: standalone)").matches);
    const onPrompt = (e: Event) => { e.preventDefault(); setEvent(e as InstallEvent); };
    const onInstalled = () => { setInstalled(true); setEvent(null); };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => { window.removeEventListener("beforeinstallprompt", onPrompt); window.removeEventListener("appinstalled", onInstalled); };
  }, []);
  return (
    <section aria-labelledby="install-heading" className={card}>
      <h2 id="install-heading" className="flex items-center gap-2 text-sm font-semibold text-fg"><Smartphone aria-hidden className="size-4 text-fg-muted" />Install the app</h2>
      <p className="mt-1 text-sm text-fg-secondary">Open CoursePilot from your home screen or dock, like an app. No notifications are sent.</p>
      <div className="mt-3">
        {installed ? (
          <p className="text-sm font-medium text-success">Installed on this device.</p>
        ) : event ? (
          <button type="button" onClick={async () => { await event.prompt(); await event.userChoice; setEvent(null); }}
            className="cp-glow inline-flex min-h-11 items-center gap-2 rounded-md border bg-primary px-4 text-sm font-semibold text-primary-foreground hover:bg-primary-hover">
            <Download aria-hidden className="size-4" />Install CoursePilot
          </button>
        ) : (
          <p className="text-xs text-fg-muted">Your browser hasn&apos;t offered installation yet. In Safari use Share, then Add to Home Screen; in Chrome or Edge use the install icon in the address bar.</p>
        )}
      </div>
    </section>
  );
}

export function ProfileOverview({ identity }: { identity: string | null }) {
  const profile = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  const programs = useQuery({ queryKey: ["programs"], queryFn: api.programs, staleTime: 3_600_000 });
  const transcripts = useQuery({ queryKey: ["imports"], queryFn: api.imports });
  const dn = useQuery({ queryKey: ["dn-imports"], queryFn: api.dnImports, staleTime: 60_000 });
  const schedules = useQuery({ queryKey: ["schedule-imports"], queryFn: api.scheduleImports });

  if (profile.isPending) return <Skeleton className="h-72" />;
  if (profile.isError) return <ErrorState onRetry={() => profile.refetch()}>Your profile couldn&apos;t be loaded.</ErrorState>;
  const p = profile.data;
  const program = p.program;
  const school = programs.data?.programs.find((x) => x.program_key === program.program_key)?.school_name;
  const homeSchool = p.home_school_code && p.home_school_code !== program.school_code
    ? programs.data?.programs.find((x) => x.school_code === p.home_school_code)?.school_name ?? `School ${p.home_school_code}` : null;
  const latest = <T,>(items: T[] | undefined, date: (t: T) => string | null | undefined) =>
    (items ?? []).map(date).filter((d): d is string => !!d).sort().pop() ?? null;
  const uploads: [string, string | null, boolean][] = [
    ["Unofficial transcript", latest(transcripts.data?.imports.filter((i) => i.state === "confirmed"), (i) => i.confirmed_at ?? i.created_at), transcripts.isPending],
    ["Degree Navigator audit", latest(dn.data?.imports.filter((i) => i.state === "confirmed"), (i) => i.confirmed_at ?? i.created_at), dn.isPending],
    ["WebReg schedule", latest(schedules.data?.imports.filter((i) => i.state === "confirmed"), (i) => i.confirmed_at ?? i.created_at ?? null), schedules.isPending],
  ];

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section aria-labelledby="about-heading" className={`${card} lg:col-span-2`}>
        <h2 id="about-heading" className="text-sm font-semibold text-fg">About you</h2>
        <dl className="mt-1 divide-y divide-border">
          <Row label="Name">{identity ?? "Signed in"}</Row>
          <Row label="School">{school ?? `School ${program.school_code}`}{homeSchool && <span className="text-fg-secondary"> (currently in {homeSchool})</span>}</Row>
          <Row label="Major">{program.program_name} <span className="text-fg-secondary">· {program.degree_type}{p.declaration_status === "undeclared" ? " · not yet declared" : ""}</span></Row>
          <Row label="Class year">{p.class_year ?? "Not stated"}{p.class_standing && <span className="text-fg-secondary"> · {STANDING[p.class_standing]}</span>}{p.first_term && <span className="text-fg-secondary"> · started {termLabel(p.first_term)}</span>}</Row>
          <Row label="Catalog year">{program.catalog_year}</Row>
          <GpaRow value={p.transcript_reported_gpa?.value ?? null} />
        </dl>
      </section>

      <section aria-labelledby="uploads-heading" className={card}>
        <h2 id="uploads-heading" className="flex items-center gap-2 text-sm font-semibold text-fg"><FileText aria-hidden className="size-4 text-fg-muted" />Uploads</h2>
        <ul className="mt-2 divide-y divide-border">
          {uploads.map(([label, date, pending]) => (
            <li key={label} className="flex items-center justify-between gap-3 py-2.5 text-sm">
              <span className="text-fg">{label}</span>
              {pending ? <Skeleton className="h-4 w-24" /> : <span className="tabular text-xs text-fg-secondary">{date ? `Confirmed ${day(date)}` : "Not uploaded"}</span>}
            </li>
          ))}
        </ul>
        <Link href="/records" className="mt-2 inline-flex min-h-10 items-center text-sm font-medium text-primary-text hover:underline">Manage records</Link>
      </section>

      <section aria-labelledby="prefs-heading" className={card}>
        <h2 id="prefs-heading" className="flex items-center gap-2 text-sm font-semibold text-fg"><Settings2 aria-hidden className="size-4 text-fg-muted" />Preferences</h2>
        <ul className="mt-2 space-y-1 text-sm">
          <li><Link className="inline-flex min-h-10 items-center font-medium text-primary-text hover:underline" href="/plan/personalize">Planning preferences (pace, workload, commitments)</Link></li>
          <li><Link className="inline-flex min-h-10 items-center font-medium text-primary-text hover:underline" href="/settings">Appearance, academic history and account</Link></li>
        </ul>
        <p className="mt-2 flex items-start gap-2 text-xs text-fg-secondary">
          <Lock aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          The assistant never receives your GPA. Grades are used only to order your own course suggestions, if you allow it below.
        </p>
      </section>
    </div>
  );
}
