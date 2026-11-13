"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, ChevronDown, FileText, GraduationCap, Plus, ScrollText } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, Skeleton, StatusBadge } from "@/components/ui/feedback";
import { Field, SearchInput, Select } from "@/components/ui/field";
import { DegreeNavigatorImport } from "@/features/degree/degree-navigator";
import { FAILURE } from "@/features/onboarding/labels";
import { ManualEntry } from "@/features/onboarding/manual-entry";
import { PlacementEditor } from "@/features/onboarding/placement-step";
import { TranscriptReview, TranscriptUpload } from "@/features/onboarding/transcript";
import { BlockedTimes, PinFields } from "@/features/planning/questionnaire-fields";
import { ScheduleImportReview } from "@/features/schedule/import-review";
import { CAMPUSES, JOB_BANDS, commitmentSummary } from "@/lib/academic/questionnaire";
import { PACE_LABEL, termLabel, upcomingTerms } from "@/lib/academic/vocabulary";
import { ApiError, api } from "@/lib/api";
import {
  CLASS_STANDINGS,
  IMPORT_CARD_LABEL,
  firstTermChoices,
  recordCardOrder,
  type RecordCardId,
  importCardState,
  programSupportLabel,
  type ImportCardState,
} from "@/lib/setup";
import { cn } from "@/lib/utils";
import type { ClassStanding, Profile, Program } from "@/types/onboarding";
import {
  NO_COMMITMENTS,
  type Commitments,
  type PlanningProfileInput,
  type PlanningProfileResponse,
} from "@/types/personalization";
import type { Pace } from "@/types/plan";

import { ChipGroup, ChoiceCard, Chip, GlowRule } from "./choices";
import { RetakeChooser } from "./retakes";

export type Draft = PlanningProfileInput;
export type SetDraft = (d: Draft) => void;

/* ================================================================ About you */

export interface AboutAnswers {
  programKey: string;
  catalogYear: string;
  declaration: "declared" | "undeclared" | null;
  firstTerm: string | null;
  classStanding: ClassStanding | null;
  pace: Pace;
  gradTerm: string | null;
  /** Prompt 08 B1: stated class year (class-scoped curricula only). */
  classYear: number | null;
  /** Prompt 08 B1: the school the student is in now (null = the program's school). */
  homeSchool: string | null;
}

export function initialAbout(profile: Profile | null | undefined, draft: Draft | null): AboutAnswers {
  return {
    programKey: profile?.program.program_key ?? "",
    catalogYear: profile?.program.catalog_year ?? "",
    declaration: profile?.declaration_status ?? null,
    firstTerm: profile?.first_term ?? null,
    classStanding: profile?.class_standing ?? null,
    pace: draft?.pace ?? "balanced",
    gradTerm: draft?.expected_graduation_term ?? null,
    classYear: profile?.class_year ?? null,
    homeSchool: profile?.home_school_code ?? null,
  };
}

const PACE_ORDER: Pace[] = ["balanced", "finish_earlier", "lighter"];

export function AboutStep({ value, onChange }: { value: AboutAnswers; onChange: (v: AboutAnswers) => void }) {
  const programs = useQuery({ queryKey: ["programs"], queryFn: api.programs });
  const all = useMemo(() => (programs.data?.programs ?? []).filter((p) =>
    p.versions.some((v) => v.support_status !== "unavailable")), [programs.data]);
  const chosen = all.find((p) => p.program_key === value.programKey);
  const schools = useMemo(() => [...new Set(all.map((p) => p.school_name))].sort(), [all]);
  const [school, setSchool] = useState<string>("");
  const [query, setQuery] = useState("");
  const activeSchool = school || chosen?.school_name || "";
  const list = all
    .filter((p) => !activeSchool || p.school_name === activeSchool)
    .filter((p) => !query.trim() || `${p.name} ${p.program_code} ${p.degree_type} ${p.variant}`
      .toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => a.name.localeCompare(b.name));
  const years = chosen?.versions.filter((v) => v.support_status !== "unavailable") ?? [];
  // Prompt 08 B1: curricula for specific classes in the chosen catalog year.
  const classYears = [...new Set(years.filter((v) => v.catalog_year === value.catalogYear)
    .flatMap((v) => v.curriculum_class_years ?? []))].sort();
  const stage = years.find((v) => v.catalog_year === value.catalogYear)?.admission_stage ?? null;
  const schoolOptions = useMemo(() => [...new Map(all.map((p) => [p.school_code, p.school_name])).entries()]
    .sort((a, b) => a[1].localeCompare(b[1])), [all]);
  const gradChoices = upcomingTerms(new Date(), 14).filter((t) => t.endsWith("1") || t.endsWith("9"));
  const set = (patch: Partial<AboutAnswers>) => onChange({ ...value, ...patch });

  return (
    <div className="space-y-6">
      <Field label="School">
        {({ id }) => (
          <Select id={id} value={activeSchool} onChange={(e) => setSchool(e.target.value)}>
            <option value="">All schools</option>
            {schools.map((s) => <option key={s} value={s}>{s}</option>)}
          </Select>
        )}
      </Field>

      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold text-fg">Your major (declared or intended)</legend>
        <SearchInput placeholder="Search programs" aria-label="Search programs" value={query}
          onChange={(e) => setQuery(e.target.value)} />
        {programs.isPending && <Skeleton className="h-32" />}
        {programs.isError && <Alert tone="danger">Programs couldn&apos;t be loaded. Please try again.</Alert>}
        <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
          {list.map((p) => (
            <ProgramChoice key={p.program_key} program={p} checked={p.program_key === value.programKey}
              onSelect={() => set({ programKey: p.program_key, catalogYear: p.versions.find((v) => v.support_status !== "unavailable")?.catalog_year ?? "", classYear: null, homeSchool: null })} />
          ))}
          {programs.isSuccess && list.length === 0 && (
            <p className="py-4 text-center text-sm text-fg-muted">No program matches. Try another school or search.</p>
          )}
        </div>
      </fieldset>

      {chosen && years.length > 1 && (
        <Field label="Catalog year" hint="The year whose rules apply to you - usually the year you started.">
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={value.catalogYear} onChange={(e) => set({ catalogYear: e.target.value })}>
              {years.map((v) => <option key={v.catalog_year} value={v.catalog_year}>{v.catalog_year}</option>)}
            </Select>
          )}
        </Field>
      )}

      {chosen && classYears.length > 0 && (
        <Field label="Your class" hint="This program has different curricula for different classes. The class you state decides which one applies to you.">
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={value.classYear ?? ""}
              onChange={(e) => set({ classYear: e.target.value ? Number(e.target.value) : null })}>
              <option value="">Choose your class</option>
              {classYears.map((y) => <option key={y} value={y}>Class of {y}</option>)}
            </Select>
          )}
        </Field>
      )}

      {chosen && stage && (
        <Field label="Which school are you in now?"
          hint={`Admission to ${chosen.school_name} is never assumed. Until it is confirmed, your current school's gen-ed stays your requirement of record.`}>
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={value.homeSchool ?? ""}
              onChange={(e) => set({ homeSchool: e.target.value || null })}>
              <option value="">{chosen.school_name}</option>
              {schoolOptions.filter(([code]) => code !== chosen.school_code).map(([code, name]) => (
                <option key={code} value={code}>{name}</option>
              ))}
            </Select>
          )}
        </Field>
      )}

      <ChipGroup legend="Have you declared this major?">
        <Chip name="declaration" label="Declared" checked={value.declaration === "declared"} onChange={() => set({ declaration: "declared" })} />
        <Chip name="declaration" label="Not yet - it's my intended major" checked={value.declaration === "undeclared"} onChange={() => set({ declaration: "undeclared" })} />
      </ChipGroup>

      <GlowRule />

      <ChipGroup legend="What year are you?" hint="Decides which records to ask you for first.">
        {CLASS_STANDINGS.map((c) => (
          <Chip key={c.value} name="class_standing" label={c.label} checked={value.classStanding === c.value}
            onChange={() => set({ classStanding: c.value })} />
        ))}
      </ChipGroup>

      <Field label="First semester at Rutgers" hint="If your record has no Rutgers semesters yet, your graduation target counts from this one.">
        {({ id, describedBy }) => (
          <Select id={id} aria-describedby={describedBy} value={value.firstTerm ?? ""} onChange={(e) => set({ firstTerm: e.target.value || null })}>
            <option value="">Not saying</option>
            {firstTermChoices(new Date()).map((t) => <option key={t} value={t}>{termLabel(t)}</option>)}
          </Select>
        )}
      </Field>

      <ChipGroup legend="When do you want to graduate?" hint="On time counts 8 semesters from your first one.">
        {PACE_ORDER.map((p) => (
          <Chip key={p} name="pace" label={PACE_LABEL[p] ?? p} checked={!value.gradTerm && value.pace === p}
            onChange={() => set({ pace: p, gradTerm: null })} />
        ))}
      </ChipGroup>
      <Field label="Or a specific term (optional)" hint="If you know it, this overrides the choice above.">
        {({ id, describedBy }) => (
          <Select id={id} aria-describedby={describedBy} value={value.gradTerm ?? ""} onChange={(e) => set({ gradTerm: e.target.value || null })}>
            <option value="">Not sure</option>
            {gradChoices.map((t) => <option key={t} value={t}>{termLabel(t)}</option>)}
          </Select>
        )}
      </Field>
    </div>
  );
}

function ProgramChoice({ program, checked, onSelect }: { program: Program; checked: boolean; onSelect: () => void }) {
  const label = programSupportLabel(program);
  return (
    <label className={cn(
      "flex min-h-14 cursor-pointer items-start gap-3 rounded-lg border bg-surface-elevated p-3 transition-[border-color,box-shadow]",
      "has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-focus",
      checked ? "cp-glow-selected" : "border-border hover:border-border-strong",
    )}>
      <input type="radio" name="program" checked={checked} onChange={onSelect} className="sr-only" />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium text-fg [overflow-wrap:anywhere]">
          {program.name}{program.variant && <span className="text-fg-secondary"> - {program.variant}</span>}
        </span>
        <span className="block text-xs text-fg-muted">{program.school_name} · {program.degree_type}</span>
      </span>
      <StatusBadge tone={label === "Beta supported" ? "success" : "warning"} label={label} className="shrink-0" />
    </label>
  );
}

/* ========================================================= Bring your record */

const CARD_TONE: Record<ImportCardState, "neutral" | "warning" | "success"> = {
  not_added: "neutral", processing: "neutral", needs_review: "warning", confirmed: "success",
};

export function RecordStep({ standing = null }: { standing?: ClassStanding | null }) {
  const client = useQueryClient();
  const transcripts = useQuery({ queryKey: ["transcript-imports"], queryFn: api.imports });
  const dn = useQuery({ queryKey: ["dn-imports"], queryFn: api.dnImports });
  const webreg = useQuery({ queryKey: ["schedule-imports"], queryFn: api.scheduleImports });
  const [open, setOpen] = useState<"transcript" | "dn" | "webreg" | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);
  const [dropError, setDropError] = useState<{ card: string; message: string } | null>(null);
  const [importId, setImportId] = useState<string | null>(null);
  const [manual, setManual] = useState(false);
  // First-years: "Have AP or transfer credit?" brings the DN card forward.
  const [showDn, setShowDn] = useState(false);

  const refresh = async () => {
    await Promise.all([["transcript-imports"], ["onboarding"], ["dn-imports"], ["dn-progress"], ["schedule-imports"],
      ["registrations"], ["audit"]].map((queryKey) => client.invalidateQueries({ queryKey })));
  };
  const openTranscript = importId ?? transcripts.data?.imports.find((i) => i.state === "extracted")?.import_id ?? null;

  async function drop(card: "transcript" | "dn" | "webreg", file: File) {
    setUploading(card);
    setDropError(null);
    try {
      if (card === "transcript") {
        const imp = await api.uploadTranscript(file);
        if (imp.state === "extracted") setImportId(imp.import_id);
        else setDropError({ card, message: FAILURE[imp.failure_code ?? ""] ?? "This transcript couldn't be used." });
      } else if (card === "dn") {
        const out = await api.uploadDegreeAudit(file);
        const failed = out.imports.find((i) => i.state === "failed");
        if (failed && !out.imports.some((i) => i.state === "extracted")) {
          setDropError({ card, message: failed.failure_message ?? "This PDF couldn't be read." });
        }
      } else {
        await api.uploadSchedule(file);
      }
      setOpen(card);
    } catch (e) {
      setDropError({ card, message: e instanceof ApiError && e.status < 500 ? (e.issues[0]?.message ?? e.message) : "The upload didn't go through. Please try again." });
    } finally {
      setUploading(null);
      await refresh();
    }
  }

  const states = (list: { state: string }[] | undefined) => (list ?? []).map((i) => i.state);
  const { main, later } = recordCardOrder(standing, showDn);
  const card: Record<RecordCardId, React.ReactNode> = {
    transcript: (
      <RecordCard
        id="transcript" icon={ScrollText} title="Unofficial transcript" what="Your completed courses."
        how={["Sign in to myRutgers yourself.", "Open your unofficial transcript and download the PDF.", "Drop it here. You check every course before it's saved."]}
        state={importCardState(states(transcripts.data?.imports), uploading === "transcript")}
        open={open === "transcript"} onToggle={() => setOpen(open === "transcript" ? null : "transcript")}
        onFile={(f) => drop("transcript", f)} error={dropError?.card === "transcript" ? dropError.message : null}
      >
        {openTranscript ? (
          <TranscriptReview importId={openTranscript}
            onConfirmed={async () => { setImportId(null); await refresh(); }}
            onDiscarded={async () => { setImportId(null); await refresh(); }} />
        ) : (
          <TranscriptUpload onUploaded={async (id) => { setImportId(id); await refresh(); }} />
        )}
        <Button variant="link" onClick={() => setManual(!manual)} aria-expanded={manual}>
          {manual ? "Hide manual entry" : "Or enter courses yourself"}
        </Button>
        {manual && <ManualEntry />}
      </RecordCard>
    ),
    dn: (
      <RecordCard
        id="dn" icon={GraduationCap} title="Degree Navigator audit (PDF)" what={standing === "transfer" ? "Shows which transfer credits Rutgers accepted, and your requirements." : "Rutgers' own view of your requirements."}
        how={["Sign in to Degree Navigator (dn.rutgers.edu) and open your audit.",
          "In Chrome, choose Print → Save as PDF (not Microsoft Print to PDF).", "Drop the PDF here and review it."]}
        state={importCardState(states(dn.data?.imports), uploading === "dn")}
        open={open === "dn"} onToggle={() => setOpen(open === "dn" ? null : "dn")}
        onFile={(f) => drop("dn", f)} error={dropError?.card === "dn" ? dropError.message : null}
      >
        <DegreeNavigatorImport />
      </RecordCard>
    ),
    webreg: (
      <RecordCard
        id="webreg" icon={CalendarDays} title="WebReg schedule (PDF)" what={standing === "first_year" ? "Upload your current schedule: the classes you registered for." : "What you're taking now."}
        how={["Sign in to WebReg and open View / Print Schedule.",
          "In Chrome, choose Print → Save as PDF (not Microsoft Print to PDF).", "Drop the PDF here and review it."]}
        state={importCardState(states(webreg.data?.imports), uploading === "webreg")}
        open={open === "webreg"} onToggle={() => setOpen(open === "webreg" ? null : "webreg")}
        onFile={(f) => drop("webreg", f)} error={dropError?.card === "webreg" ? dropError.message : null}
      >
        <ScheduleImportReview />
      </RecordCard>
    ),
  };
  return (
    <div className="space-y-4" data-standing={standing ?? "unstated"}>
      {main.map((id) => <div key={id} data-card={id}>{card[id]}</div>)}
      {standing === "first_year" && !showDn && (
        <p className="text-sm text-fg-secondary">
          Have AP or transfer credit?{" "}
          <Button variant="link" onClick={() => setShowDn(true)}>Upload your Degree Navigator audit now</Button>
        </p>
      )}
      {later.length > 0 && (
        <details className="group rounded-lg border border-border bg-surface" data-later>
          <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 px-4 text-sm font-medium text-fg [&::-webkit-details-marker]:hidden">
            Later — we&apos;ll remind you after your grades come in
            <ChevronDown aria-hidden className="size-4 text-fg-muted transition-transform group-open:rotate-180" />
          </summary>
          <div className="space-y-4 p-3">
            {later.map((id) => <div key={id} data-card={id}>{card[id]}</div>)}
          </div>
        </details>
      )}

      <details className="group rounded-lg border border-border bg-surface-elevated">
        <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 px-4 text-sm font-medium text-fg [&::-webkit-details-marker]:hidden">
          Took the Rutgers math placement test? (optional)
          <ChevronDown aria-hidden className="size-4 text-fg-muted transition-transform group-open:rotate-180" />
        </summary>
        <div className="px-1 pb-1"><PlacementEditor /></div>
      </details>

      <p className="text-sm text-fg-muted">
        All optional. Without your Degree Navigator audit, your plan is labelled <span className="font-medium text-fg-secondary">estimated</span>.
        Files are read, then deleted; only what you confirm is kept.
      </p>
    </div>
  );
}

export function RecordCard({ id, icon: Icon, title, what, how, state, open, onToggle, onFile, error, children }: {
  id: string; icon: typeof FileText; title: string; what: string; how: string[]; state: ImportCardState;
  open: boolean; onToggle: () => void; onFile: (f: File) => void; error: string | null; children: React.ReactNode;
}) {
  const [over, setOver] = useState(false);
  const panelId = `record-${id}-panel`;
  return (
    <section
      aria-labelledby={`record-${id}-title`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files?.[0]; if (f) onFile(f); }}
      data-state={state}
      className={cn("rounded-lg border bg-surface-elevated transition-[border-color,box-shadow]",
        over ? "cp-glow-selected" : "border-border")}
    >
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start">
        <Icon aria-hidden className="size-6 shrink-0 text-primary" />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 id={`record-${id}-title`} className="text-base font-semibold text-fg">{title}</h3>
            <StatusBadge tone={CARD_TONE[state]} label={IMPORT_CARD_LABEL[state]} />
          </div>
          <p className="text-sm text-fg-secondary">{what}</p>
          {!open && (
            <ol className="list-decimal space-y-0.5 pl-5 pt-1 text-sm text-fg-muted">
              {how.map((h) => <li key={h}>{h}</li>)}
            </ol>
          )}
          {!open && <p className="pt-1 text-xs text-fg-muted">Drag a PDF onto this card, or open it to choose a file.</p>}
        </div>
        <Button variant={state === "not_added" ? "primary" : "secondary"} size="md" onClick={onToggle}
          aria-expanded={open} aria-controls={panelId} className="sm:shrink-0">
          {open ? "Close" : state === "needs_review" ? "Review" : state === "confirmed" ? "View" : <><Plus aria-hidden className="size-4" /> Add</>}
          {open && <ChevronDown aria-hidden className="size-4 rotate-180" />}
        </Button>
      </div>
      {error && <div className="px-4 pb-3"><Alert tone="danger">{error}</Alert></div>}
      {open && <div id={panelId} className="space-y-4 border-t border-border p-4">{children}</div>}
    </section>
  );
}

/* ============================================================ How intense? */

export function IntensityStep({ meta, draft, setDraft }: { meta: PlanningProfileResponse; draft: Draft; setDraft: SetDraft }) {
  const level = draft.workload_level ?? "balanced";
  return (
    <div className="space-y-4">
      <div role="radiogroup" aria-label="Semester intensity" className="grid gap-3 sm:grid-cols-2">
        {(meta.workload_levels ?? []).map((w) => (
          <ChoiceCard key={w.value} name="workload_level" checked={level === w.value}
            onSelect={() => setDraft({ ...draft, workload_level: w.value })}
            title={w.label} detail={levelLine(w)} body={w.budget ? `Workload budget up to ${Number(w.budget)} a semester.` : undefined} />
        ))}
      </div>
      {meta.workload_weight && <p className="text-xs text-fg-muted">{meta.workload_weight}</p>}
    </div>
  );
}

/** "15-18 credits · 2-3 major courses". */
export function levelLine(w: { credit_floor: string; credit_max: string; major_min?: number; major_max?: number }): string {
  const credits = `${Number(w.credit_floor)}-${Number(w.credit_max)} credits`;
  if (w.major_min === undefined || w.major_max === undefined) return credits;
  const major = w.major_min === w.major_max ? `${w.major_min}` : `${w.major_min}-${w.major_max}`;
  return `${credits} · ${major} major course${w.major_max === 1 ? "" : "s"}`;
}

/* =============================================================== Your week */

const HOUR_CHIPS = [0, 5, 10, 20];

export function WeekStep({ meta, draft, setDraft }: { meta: PlanningProfileResponse; draft: Draft; setDraft: SetDraft }) {
  const c: Commitments = draft.commitments ?? NO_COMMITMENTS;
  const set = (patch: Partial<Commitments>) => setDraft({ ...draft, commitments: { ...c, ...patch } });
  const hours = (key: "athletics_hours_per_week" | "research_hours_per_week" | "recruiting_hours_per_week", legend: string) => {
    const options = HOUR_CHIPS.includes(c[key]) ? HOUR_CHIPS : [...HOUR_CHIPS, c[key]].sort((a, b) => a - b);
    return (
      <ChipGroup legend={legend}>
        {options.map((h) => (
          <Chip key={h} name={key} label={h === 0 ? "None" : `${h} hrs/week`} checked={c[key] === h} onChange={() => set({ [key]: h })} />
        ))}
      </ChipGroup>
    );
  };
  return (
    <div className="space-y-5">
      <ChipGroup legend="A job?">
        {JOB_BANDS.map((b) => (
          <Chip key={b.hours} name="job" label={b.label} checked={c.job_hours_per_week === b.hours} onChange={() => set({ job_hours_per_week: b.hours })} />
        ))}
      </ChipGroup>
      <ChipGroup legend="Greek life">
        {(meta.greek_life ?? []).map((g) => (
          <Chip key={g.value} name="greek" label={g.label} checked={c.greek_life === g.value} onChange={() => set({ greek_life: g.value })} />
        ))}
      </ChipGroup>
      <ChipGroup legend="Clubs and organizations">
        {(meta.clubs ?? []).map((k) => (
          <Chip key={k.value} name="clubs" label={k.label} checked={c.clubs === k.value} onChange={() => set({ clubs: k.value })} />
        ))}
      </ChipGroup>
      {hours("athletics_hours_per_week", "Athletics")}
      {hours("research_hours_per_week", "Research")}
      {hours("recruiting_hours_per_week", "Recruiting")}
      <p aria-live="polite" className="rounded-md bg-primary-subtle px-3 py-2 text-sm text-fg">{commitmentSummary(c)}.</p>
      <GlowRule />
      <ChipGroup legend="In an honors program?" hint="Honors-only courses are planned only if you are.">
        <Chip name="honors" label="Yes" checked={draft.honors === true} onChange={() => setDraft({ ...draft, honors: true })} />
        <Chip name="honors" label="No" checked={draft.honors === false} onChange={() => setDraft({ ...draft, honors: false })} />
        <Chip name="honors" label="Skip" checked={draft.honors == null} onChange={() => setDraft({ ...draft, honors: null })} />
      </ChipGroup>
    </div>
  );
}

/* ========================================================== Schedule shape */

export function ScheduleStep({ meta, draft, setDraft }: { meta: PlanningProfileResponse; draft: Draft; setDraft: SetDraft }) {
  const s = draft.schedule;
  const setS = (patch: Partial<Draft["schedule"]>) => setDraft({ ...draft, schedule: { ...s, ...patch } });
  const campuses = s.preferred_campuses ?? [];
  const blocked = s.blocked_times ?? [];
  const [adding, setAdding] = useState(blocked.length > 0);
  return (
    <div className="space-y-5">
      <ChipGroup legend="When do you not want class?">
        <Chip type="checkbox" name="early" label="No class before 10am" checked={s.earliest_start === "10:00"}
          onChange={(on) => setS({ earliest_start: on ? "10:00" : null })} />
        <Chip type="checkbox" name="fri" label="No Fridays" checked={s.avoid_days.includes("F")}
          onChange={(on) => setS({ avoid_days: on ? [...s.avoid_days, "F"] : s.avoid_days.filter((d) => d !== "F") })} />
        <Chip type="checkbox" name="eve" label="No evenings" checked={s.latest_end === "18:00"}
          onChange={(on) => setS({ latest_end: on ? "18:00" : null })} />
      </ChipGroup>
      <ChipGroup legend="Preferred campuses" hint="Listed first when you build a schedule; nothing is ruled out.">
        {CAMPUSES.map((c) => (
          <Chip key={c.code} type="checkbox" name={`campus-${c.code}`} label={c.label} checked={campuses.includes(c.code)}
            onChange={(on) => setS({ preferred_campuses: on ? [...campuses, c.code] : campuses.filter((x) => x !== c.code) })} />
        ))}
      </ChipGroup>
      <GlowRule />
      <div className="space-y-2">
        <p className="text-sm font-semibold text-fg">Fixed weekly times</p>
        <p className="text-xs text-fg-muted">Job shifts, meetings and other set times. No class is placed during them.</p>
        {adding ? (
          <BlockedTimes value={blocked} onChange={(b) => setS({ blocked_times: b })} />
        ) : (
          <Button variant="secondary" onClick={() => setAdding(true)}><Plus aria-hidden className="size-4" /> Add a time</Button>
        )}
      </div>
      <GlowRule />
      <ChipGroup legend="For the rest of your credits" hint="After the courses you need, how should the remaining ones be?">
        {(meta.filler_preferences ?? []).map((f) => (
          <Chip key={f.value} name="filler" label={f.label} checked={(draft.filler_preference ?? "no_preference") === f.value}
            onChange={() => setDraft({ ...draft, filler_preference: f.value })} />
        ))}
      </ChipGroup>
    </div>
  );
}

/* =================================================================== Musts */

export function MustsStep({ draft, setDraft }: { draft: Draft; setDraft: SetDraft }) {
  const terms = upcomingTerms(new Date(), 8, { summer: draft.include_summer, winter: draft.include_winter });
  const all = draft.pins ?? [];
  const retakePins = all.filter((p) => p.retake);
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <p className="text-sm font-semibold text-fg">Retakes</p>
        <RetakeChooser pins={all} onChange={(pins) => setDraft({ ...draft, pins })} />
      </div>
      <GlowRule />
      <div className="space-y-2">
        <p className="text-sm font-semibold text-fg">Pin a course to a semester</p>
        <p className="text-xs text-fg-muted">It goes in that semester or not at all, and your plan says why.</p>
        <PinFields value={all.filter((p) => !p.retake)} terms={terms}
          onChange={(pins) => setDraft({ ...draft, pins: [...retakePins.filter((r) => !pins.some((p) => p.course === r.course)), ...pins] })} />
      </div>
    </div>
  );
}
