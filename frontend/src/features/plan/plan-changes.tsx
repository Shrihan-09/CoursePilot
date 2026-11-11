"use client";

import { useAssistantPanel } from "@/features/assistant/assistant-panel";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Lock, LockOpen, MapPin, Monitor, Search, Sparkles, Undo2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, Badge, Skeleton } from "@/components/ui/feedback";
import { Field, SearchInput, Select } from "@/components/ui/field";
import { Dialog, Sheet, useToast } from "@/components/ui/overlay";
import { ApiError, api } from "@/lib/api";
import { deliveryText, termLabel } from "@/lib/academic/vocabulary";
import { cn } from "@/lib/utils";
import type {
  ChoiceQuestion,
  CourseOption,
  CourseOptions,
  CourseSearchResult,
  OptionGroup,
  PlanChange,
  PlanDiff,
  PlanPreview,
  SavedPlan,
  SearchHit,
} from "@/types/plan";

/**
 * Prompt 11 Part A: options, swaps, locks and undo - the student decides.
 *
 * Every change goes through the same two steps: PREVIEW (the planning engine
 * re-runs with the change; nothing is saved) and CONFIRM (applied only if the
 * plan has not changed since the preview). No wording here rates a course as
 * easy or hard; workload is shown as its published inputs.
 */

const GATE_TEXT: Record<string, string> = {
  prerequisites_not_met: "prerequisites not met by then",
  prerequisites_need_confirmation: "prerequisites Coursenav can't verify",
  needs_a_corequisite_in_the_same_term: "needs a co-requisite in the same semester",
  already_taken: "already on your record",
  already_planned: "already in your plan",
  cross_listed_twin_taken_or_planned: "the same class under another number is in your plan or record",
  excluded_by_program: "your program gives no credit for it",
  you_removed_it_from_this_term: "you removed it from this semester",
  variable_credit: "variable credit",
  honors_only: "open only to honors programs",
  special_permission_required: "every section needs special permission",
  restricted_sections: "every section is restricted to other majors or schools",
  core_code_not_listed_for_term: "this semester's listing doesn't certify the Core goal",
  over_credit_cap: "would go over this semester's credit maximum",
  not_offered_in_term: "not listed for this semester",
  offered_in_other_seasons_only: "offered in other seasons only",
  no_offering_evidence: "no record of Rutgers offering it",
  target_term_data_pending: "this semester's data isn't loaded yet",
};

export const gateText = (code: string) => GATE_TEXT[code] ?? code.replace(/_/g, " ");
/** "locked: you locked ..." -> "you locked ..." (the code is for programs). */
const message = (error: string) => error.replace(/^[a-z_]+:\s*/, "");

/* ------------------------------------------------------------ the flow */

export function usePlanChanges() {
  const client = useQueryClient();
  const toast = useToast();
  const [preview, setPreview] = useState<PlanPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const ask = useMutation({
    mutationFn: (change: PlanChange) => api.previewChange(change),
    onMutate: () => setError(null),
    onSuccess: (p) => setPreview(p),
    onError: (e) => setError(e instanceof ApiError ? e.message : "The preview couldn't be computed."),
  });
  const settle = (plan: SavedPlan) => {
    client.setQueryData(["plan"], { plan });
    void client.invalidateQueries({ queryKey: ["plan-choices"] });
    void client.invalidateQueries({ queryKey: ["course-options"] });
  };
  const confirm = useMutation({
    mutationFn: (id: string) => api.applyChange(id),
    onSuccess: (plan) => {
      settle(plan);
      toast(`Saved: ${preview?.diff?.summary ?? "your change"}.`, "success", {
        label: "Undo", onClick: () => undo.mutate(),
      });
      setPreview(null);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "The change couldn't be saved."),
  });
  const undo = useMutation({
    mutationFn: () => api.undoChange(),
    onSuccess: (plan) => { settle(plan); toast("Restored the previous version of your plan."); },
    onError: (e) => toast(e instanceof ApiError ? e.message : "Nothing to undo.", "danger"),
  });
  const request = useCallback((change: PlanChange) => ask.mutate(change), [ask]);
  return {
    request,
    preview,
    previewing: ask.isPending,
    confirm: () => preview && confirm.mutate(preview.preview_id),
    confirming: confirm.isPending,
    cancel: () => { setPreview(null); setError(null); },
    undo: () => undo.mutate(),
    undoing: undo.isPending,
    error,
  };
}

export type PlanChanges = ReturnType<typeof usePlanChanges>;

/* ------------------------------------------------------------ the preview */

export function ChangePreviewDialog({ changes }: { changes: PlanChanges }) {
  const p = changes.preview;
  if (!p) return null;
  const d = p.diff;
  return (
    <Dialog
      open
      onClose={changes.cancel}
      title={p.valid ? "Preview your change" : "This change can't be made"}
      description={p.valid ? "Nothing is saved until you confirm." : undefined}
      footer={p.valid ? (
        <>
          <Button variant="ghost" onClick={changes.cancel}>Not now</Button>
          <Button variant="primary" loading={changes.confirming} onClick={changes.confirm}>Confirm</Button>
        </>
      ) : <Button onClick={changes.cancel}>Close</Button>}
    >
      {!p.valid && (
        <ul className="list-disc space-y-1 pl-5 text-sm text-fg">
          {p.errors.map((e) => <li key={e}>{message(e)}</li>)}
        </ul>
      )}
      {changes.error && <Alert tone="danger" title="Not saved">{changes.error}</Alert>}
      {d && <PlanDiffView diff={d} />}
    </Dialog>
  );
}

/** A preview's effect: per-term before -> after, forced moves, graduation,
 * requirements. Shared by the Confirm dialog and the assistant's proposal cards. */
export function PlanDiffView({ diff: d }: { diff: PlanDiff }) {
  const changed = d.terms.filter((t) => t.added.length || t.removed.length);
  return (
    <div className="space-y-4 text-sm" data-testid="change-preview">
      <p className="font-medium text-fg">{d.summary}</p>
      {d.big && (
        <Alert tone="warning" title="This change affects more than one course">
          Review what else moves before you confirm.
        </Alert>
      )}
      {changed.map((t) => (
        <section key={t.term_code} className="space-y-1 rounded border border-border p-3">
          <h3 className="flex flex-wrap items-baseline justify-between gap-2 font-semibold text-fg">
            {t.label}
            <span className="tabular text-xs font-normal text-fg-secondary">
              {Number(t.credits_before)} → {Number(t.credits_after)} credits
              {t.workload_after != null && t.budget != null &&
                ` · workload ${Number(t.workload_before ?? 0)} → ${Number(t.workload_after)} of ${Number(t.budget)}`}
            </span>
          </h3>
          <ul className="space-y-0.5">
            {t.removed.map((c) => (
              <li key={`-${c}`} className="text-fg-secondary">
                <span aria-hidden className="mr-1 font-semibold text-danger">−</span>
                <span className="sr-only">Removed: </span>
                <span className="code">{c}</span> {t.before.find((x) => x.course === c)?.title}
              </li>
            ))}
            {t.added.map((c) => (
              <li key={`+${c}`} className="text-fg">
                <span aria-hidden className="mr-1 font-semibold text-success">+</span>
                <span className="sr-only">Added: </span>
                <span className="code">{c}</span> {t.after.find((x) => x.course === c)?.title}
              </li>
            ))}
          </ul>
        </section>
      ))}
      {d.forced_moves.length > 0 && (
        <section className="space-y-1">
          <h3 className="text-xs font-medium uppercase tracking-wide text-fg-muted">Also changes</h3>
          <ul className="space-y-1">
            {d.forced_moves.map((m) => (
              <li key={m.course} className="text-fg-secondary">
                <span className="code text-fg">{m.course}</span> {m.title}:{" "}
                {m.from_term ? termLabel(m.from_term) : "not planned"} <ArrowRight aria-label="to" className="inline size-3" />{" "}
                {m.to_term ? termLabel(m.to_term) : "out of the plan"} - {m.reason}
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="text-fg-secondary">
        Last planned semester: {d.last_term_before ? termLabel(d.last_term_before) : "-"}
        {d.last_term_after !== d.last_term_before && <> → <strong className="text-fg">{d.last_term_after ? termLabel(d.last_term_after) : "-"}</strong></>}
        {d.graduation_after && <> · graduation target {termLabel(d.graduation_after)}</>}
      </p>
      {d.requirements_lost.length > 0 && (
        <p className="font-medium text-warning">No longer completed by this plan: {d.requirements_lost.join(", ")}</p>
      )}
      {d.requirements_gained.length > 0 && (
        <p className="text-success">Now completed: {d.requirements_gained.join(", ")}</p>
      )}
      {d.new_issues.map((i) => <p key={i} className="text-warning">{i}</p>)}
    </div>
  );
}

/* ------------------------------------------------------------ one option */

export function OptionCard({ option: o, onChoose, busy, action = "Choose" }: {
  option: CourseOption | SearchHit; onChoose: () => void; busy?: boolean; action?: string;
}) {
  const open = "open_status" in o ? o.open_status : "unknown";
  const DeliveryIcon = o.delivery.some((d) => d === "async" || d === "online") ? Monitor : MapPin;
  return <li className="min-w-0 rounded-lg border border-border bg-surface-elevated p-3 text-sm" data-testid="option-card">
    <div className="flex min-w-0 flex-wrap items-start justify-between gap-2"><div className="min-w-0 flex-1 basis-36"><h4 className="font-semibold leading-snug [overflow-wrap:anywhere]">{o.title}</h4><p className="code mt-1 text-xs text-fg-muted">{o.course} / {Number(o.credits)} credits</p></div><Button size="sm" variant="secondary" loading={busy} onClick={onChoose}>{action}</Button></div>
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-fg-secondary">{o.requirements[0] && <Badge tone="info">{o.requirements[0]}</Badge>}<span><DeliveryIcon aria-hidden className="mr-1 inline size-3" />{deliveryText(o.delivery)}</span><span>{open === "open" ? "Open" : open === "closed" ? "Closed" : "Seats unknown"}</span></div>
    <details className="mt-1 text-xs text-fg-secondary"><summary className="cursor-pointer py-1">Details</summary><p>{o.offering === "confirmed" ? "Confirmed for this semester" : "Historically offered this season"}</p>{o.requirements.length > 1 && <p>Also fills: {o.requirements.slice(1).join(", ")}</p>}{"why" in o && <p>{o.why}</p>}{o.similar_to && <p>{o.similar_to}</p>}{"impact" in o && <p>{o.impact}</p>}<p>{o.workload_weight != null && `Workload ${Number(o.workload_weight)}`}{o.contact_hours != null && ` / ${Number(o.contact_hours)} contact hrs/week`}</p></details>
  </li>;
}

function Group({ group, onChoose, busy }: { group: OptionGroup; onChoose: (o: CourseOption) => void; busy: boolean }) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-medium uppercase tracking-wide text-fg-muted">
        {group.kind === "same_requirement" ? "Same requirement" : "A different requirement instead"}: {group.requirement_name}
      </h3>
      {group.options.length === 0 ? (
        <p className="text-sm text-fg-secondary">No other course passes every check for this semester.</p>
      ) : (
        <ul className="grid gap-2">{group.options.map((o) => (
          <OptionCard key={o.course} option={o} busy={busy} onChoose={() => onChoose(o)} />
        ))}</ul>
      )}
    </section>
  );
}

/* ------------------------------------------------------------ options sheet */

export function CourseOptionsSheet({ target, onClose, changes }: {
  target: { course: string; term: string } | null;
  onClose: () => void;
  changes: PlanChanges;
}) {
  const assistant = useAssistantPanel();
  const q = useQuery({
    queryKey: ["course-options", target?.course, target?.term],
    queryFn: () => api.courseOptions(target!.course, target!.term),
    enabled: !!target,
    staleTime: 0,
  });
  const [moveTo, setMoveTo] = useState("");
  useEffect(() => setMoveTo(""), [target?.course, target?.term]);
  if (!target) return null;
  const o: CourseOptions | undefined = q.data;
  const run = (change: PlanChange) => { changes.request(change); onClose(); };
  return (
    <Sheet open onClose={onClose} title={`Options for ${target.course}`}>
      <Button size="sm" variant="secondary" onClick={() => {
        onClose();
        assistant?.open({ term: target.term, course: target.course,
                          draft: `Help me pick something instead of ${target.course}` });
      }}>Ask Coursenav about alternatives</Button>
      {q.isPending ? <Skeleton className="h-64" /> : q.isError ? (
        <Alert tone="danger" title="Options couldn't be loaded">{q.error instanceof ApiError ? q.error.message : "Try again."}</Alert>
      ) : o && (
        <div className="space-y-5" data-testid="options-sheet">
          <div className="space-y-1">
            <p className="text-sm text-fg">{o.title}</p>
            <p className="text-xs text-fg-secondary">
              {termLabel(o.term_code)}{o.requirements.length ? ` · counts toward ${o.requirements.join(", ")}` : ""}
            </p>
            <StateLine state={o.state} reason={o.locked_reason} />
          </div>
          {o.locked && (
            <Alert tone="info" title={o.locked_reason === "anchor" ? "Required here" : "Locked by you"}>
              {o.locked_reason === "anchor"
                ? "This course is an anchor (a required retake or one of your Musts), so it has no swap. It can move to another valid semester."
                : "Unlock it to see other options. Coursenav never replaces a course you locked."}
            </Alert>
          )}
          {o.groups.map((g) => (
            <Group key={`${g.kind}-${g.requirement_code}`} group={g} busy={changes.previewing}
              onChoose={(opt) => run({ kind: "swap", course: o.course, term_code: o.term_code, replacement: opt.course })} />
          ))}
          {!o.locked && o.groups.length === 0 && (
            <p className="text-sm text-fg-secondary">No other course passes every check for {termLabel(o.term_code)}.</p>
          )}
          {!o.locked && <CourseSearchBox term={o.term_code} changes={{ ...changes, request: run }} replacing={o.course} inputId="course-options-search" />}
          <div className="space-y-2 border-t border-border pt-4">
            {o.move_terms.length > 0 && (
              <div className="flex items-end gap-2">
                <Field label="Move to another semester">
                  {({ id }) => (
                    <Select id={id} value={moveTo} onChange={(e) => setMoveTo(e.target.value)}>
                      <option value="">Choose a semester</option>
                      {o.move_terms.map((t) => <option key={t} value={t}>{termLabel(t)}</option>)}
                    </Select>
                  )}
                </Field>
                <Button size="sm" disabled={!moveTo} loading={changes.previewing}
                  onClick={() => run({ kind: "move", course: o.course, term_code: o.term_code, to_term: moveTo })}>Preview move</Button>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              {o.state === "planner_selected" && (
                <Button size="sm" variant="secondary" loading={changes.previewing}
                  onClick={() => run({ kind: "lock", course: o.course, term_code: o.term_code })}>
                  <Lock aria-hidden className="size-4" /> Lock to {termLabel(o.term_code)}
                </Button>
              )}
              {(o.locked_reason === "locked" || o.state === "user_selected") && (
                <Button size="sm" variant="secondary" loading={changes.previewing}
                  onClick={() => run({ kind: "unlock", course: o.course, term_code: o.term_code })}>
                  <LockOpen aria-hidden className="size-4" /> Unlock
                </Button>
              )}
              {o.can_remove && (
                <Button size="sm" variant="ghost" loading={changes.previewing}
                  onClick={() => run({ kind: "remove", course: o.course, term_code: o.term_code })}>
                  Remove from {termLabel(o.term_code)}
                </Button>
              )}
            </div>
          </div>
          {o.not_available.length > 0 && (
            <details className="text-sm">
              <summary className="min-h-10 cursor-pointer text-fg-secondary">Not available this semester ({o.not_available.length})</summary>
              <ul className="mt-1 space-y-0.5 text-fg-secondary">
                {o.not_available.map((x) => <li key={x.course}><span className="code">{x.course}</span>: {gateText(x.reason)}</li>)}
              </ul>
            </details>
          )}
        </div>
      )}
    </Sheet>
  );
}

export function StateLine({ state, reason }: { state: string; reason: string | null }) {
  if (state === "user_selected") return <Badge tone="success">Your choice</Badge>;
  if (state === "locked") return <Badge tone="info"><Lock aria-hidden className="size-3" />{reason === "anchor" ? "Anchor" : "Locked"}</Badge>;
  return null;
}

/* ------------------------------------------------------------ "Your choices" */

export function YourChoicesCard({ changes, revision = 0, onBuild }: { changes: PlanChanges; revision?: number; onBuild?: (term: string) => void }) {
  const q = useQuery({ queryKey: ["plan-choices"], queryFn: api.planChoices, staleTime: 30_000 });
  const [kept, setKept] = useState<string[]>([]);
  const [review, setReview] = useState<boolean | null>(null);
  const [selected, setSelected] = useState<ChoiceQuestion | null>(null);
  const [groupIndex, setGroupIndex] = useState(0);
  const [more, setMore] = useState(false);
  useEffect(() => { setKept([]); setReview(null); setSelected(null); }, [revision]);
  if (q.isPending) return <Skeleton className="h-24" />;
  if (q.isError || !q.data.term_code) return null;
  const term = q.data.term_code;
  const key = (x: ChoiceQuestion) => `${x.kind}:${x.course ?? x.requirement_code}`;
  const fixed = (x: ChoiceQuestion) => x.kind === "already_set" || x.kind === "your_choice";
  const allSet = q.data.questions.every((x) => fixed(x) || kept.includes(key(x)));
  const expanded = review ?? !allSet;
  const group = selected?.groups[groupIndex];
  const choose = (change: PlanChange) => { changes.request(change); setSelected(null); };
  return <section className="min-w-0 rounded-xl border border-primary/25 bg-surface-elevated p-3" data-testid="your-choices">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 id="choices-heading" className="text-sm font-semibold">{allSet ? `All set for ${termLabel(term)}` : `Your choices for ${termLabel(term)}`}</h2>
      <Button size="sm" variant="ghost" aria-expanded={expanded} onClick={() => setReview(!expanded)}>{expanded ? "Collapse" : "Review"}</Button>
    </div>
    {expanded && <div className="divide-y divide-border">
      {q.data.questions.map((x) => <div key={key(x)} className="flex min-w-0 flex-wrap items-center justify-between gap-2 py-2 text-sm">
        <p className="min-w-0 flex-1 basis-40 [overflow-wrap:anywhere]">{fixed(x) ? <><Lock aria-hidden className="mr-1 inline size-3" />{x.prompt.replace(/^Your choice:/, "Already set:")}</> : <>{x.kind === "alternative" ? x.prompt : `${x.groups[0]?.requirement_name ?? "Core slot"} / pick 1`}<span className="block text-xs text-fg-secondary">Coursenav picked {x.title ?? x.alternatives.find((a) => x.recommended.includes(a.code))?.name ?? "the recommended option"}</span></>}</p>
        {!fixed(x) && <div className="flex gap-1"><Button size="sm" variant="ghost" onClick={() => { setKept([...kept, key(x)]); setReview(null); }}>{kept.includes(key(x)) ? "Kept" : "Keep"}</Button><Button size="sm" variant="secondary" onClick={() => { setSelected(x); setGroupIndex(0); setMore(false); }}>Change</Button></div>}
      </div>)}
    </div>}
    <Dialog open={!!selected} onClose={() => setSelected(null)} title="Change your choice" description="Choose a course, then review its impact before confirming.">
      {selected?.kind === "alternative" ? <div className="grid gap-2">{selected.alternatives.map((a) => <Button key={a.code} className="h-auto whitespace-normal text-left" onClick={() => choose({ kind: "choose_alternative", requirement_code: selected.requirement_code, alternatives: [a.code] })}>{a.name}{selected.recommended.includes(a.code) ? " (recommended)" : ""}</Button>)}</div> : <>
        <div role="group" aria-label="Requirement groups" className="flex flex-wrap gap-1">{selected?.groups.map((g, i) => <Button key={`${g.kind}-${g.requirement_code}`} size="sm" variant={i === groupIndex ? "primary" : "secondary"} aria-pressed={i === groupIndex} onClick={() => { setGroupIndex(i); setMore(false); }}>{g.requirement_name}</Button>)}</div>
        <ul className="mt-3 grid min-w-0 gap-2">{group?.options.slice(0, more ? undefined : 3).map((o) => <OptionCard key={o.course} option={o} busy={changes.previewing} onChoose={() => choose({ kind: "swap", course: selected!.course, term_code: term, replacement: o.course })} />)}</ul>
        {!group?.options.length && <p className="py-3 text-sm text-fg-secondary">No alternatives pass every check for this group.</p>}
        {!more && (group?.options.length ?? 0) > 3 && <Button size="sm" variant="ghost" onClick={() => setMore(true)}>Show more</Button>}
      </>}
      {onBuild && <Button className="mt-3 h-auto whitespace-normal" variant="secondary" onClick={() => { setSelected(null); onBuild(term); }}>Browse all in the semester builder</Button>}
    </Dialog>
  </section>;
}

/* ------------------------------------------------------------ search */

export function AddCourseSheet({ term, changes, onClose }: { term: string | null; changes: PlanChanges; onClose: () => void }) {
  if (!term) return null;
  return <Sheet open title={`Add a course to ${termLabel(term)}`} onClose={onClose}>
    <p className="mb-3 text-sm text-fg-secondary">Search for a course that counts. Review the plan impact before confirming.</p>
    <CourseSearchBox term={term} inputId="add-course-search" changes={{ ...changes, request: (change) => { changes.request(change); onClose(); } }} />
  </Sheet>;
}

export function CourseSearchBox({ term, changes, replacing, inputId = "course-search" }: {
  term: string; changes: PlanChanges; replacing?: string; inputId?: string;
}) {
  const [text, setText] = useState("");
  const search = useMutation({
    mutationFn: (body: { text: string; requirements?: string[][] }) => api.searchCourses({
      ...body, term_code: /\b(spring|summer|fall|winter)\b/i.test(body.text) ? undefined : term, limit: 8,
    }),
  });
  const r: CourseSearchResult | undefined = search.data;
  const shownTerm = r?.interpretation.term_code ?? term;
  return (
    <div className="space-y-3" data-testid="course-search">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (text.trim()) search.mutate({ text }); }}>
        <label htmlFor={inputId} className="sr-only">Add a specific course</label>
        <SearchInput id={inputId} value={text} onChange={(e) => setText(e.target.value)}
          placeholder='e.g. "an online history course" or "Fridays off: what Core fits?"' />
        <Button type="submit" loading={search.isPending}><Search aria-hidden className="size-4" /> Search</Button>
      </form>
      {search.isError && <Alert tone="danger" title="Search failed">{search.error instanceof ApiError ? search.error.message : "Try again."}</Alert>}
      {r && (
        <div className="space-y-2">
          <p className="flex flex-wrap gap-1 text-xs text-fg-secondary">
            <span>Looking for:</span>
            <Badge>{termLabel(r.interpretation.term_code)}</Badge>
            {r.interpretation.requirement_names.map((n) => <Badge key={n} tone="info">{n}</Badge>)}
            {r.interpretation.topic && <Badge>topic: {r.interpretation.topic}</Badge>}
            {r.interpretation.online && <Badge>{r.interpretation.async_only ? "async" : "online"}</Badge>}
            {r.interpretation.in_person && <Badge>in person</Badge>}
            {r.interpretation.campus && <Badge>{r.interpretation.campus}</Badge>}
            {r.interpretation.earliest && <Badge>after {r.interpretation.earliest}</Badge>}
            {r.interpretation.latest && <Badge>before {r.interpretation.latest}</Badge>}
            {r.interpretation.avoid_days.length > 0 && <Badge>no {r.interpretation.avoid_days.join("/")}</Badge>}
            {r.interpretation.exclude.map((x) => <Badge key={x}>not {x}</Badge>)}
          </p>
          {r.interpretation.notes.map((n) => <p key={n} className="text-xs text-fg-secondary">{n}</p>)}
          {r.interpretation.did_you_mean.map((d) => (
            <div key={d.phrase} className="space-y-1">
              <p className="text-sm text-fg">Did you mean…</p>
              <div className="flex flex-wrap gap-2">
                {d.options.map((o) => (
                  <Button key={o.requirements.join()} size="sm" variant="secondary"
                    onClick={() => search.mutate({ text: text.replace(new RegExp(d.phrase, "i"), ""), requirements: [o.requirements] })}>
                    {o.names.join(" / ")}
                  </Button>
                ))}
              </div>
            </div>
          ))}
          {r.results.length > 0 ? (
            <ul className="grid gap-2 sm:grid-cols-2">
              {r.results.map((h) => (
                <OptionCard key={h.course} option={h} busy={changes.previewing}
                  action={replacing ? "Preview swap" : h.planned_term ? `Move here from ${termLabel(h.planned_term)}` : `Add to ${termLabel(shownTerm)}`}
                  onChoose={() => changes.request(replacing
                    ? { kind: "swap", course: replacing, term_code: term, replacement: h.course }
                    : h.planned_term
                    ? { kind: "move", course: h.course, term_code: h.planned_term, to_term: shownTerm }
                    : { kind: "add", course: h.course, term_code: shownTerm })} />
              ))}
            </ul>
          ) : r.interpretation.did_you_mean.length === 0 && (
            <p className="text-sm text-fg-secondary">No course passes every check for {termLabel(shownTerm)} with those filters.</p>
          )}
          {r.not_available.length > 0 && (
            <details className="text-sm">
              <summary className="min-h-10 cursor-pointer text-fg-secondary">Not available this semester ({r.not_available.length})</summary>
              <ul className="mt-1 space-y-0.5 text-fg-secondary">
                {r.not_available.map((x) => <li key={x.course}><span className="code">{x.course}</span>: {gateText(x.reason)}</li>)}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ undo bar */

export function UndoBar({ plan, changes }: { plan: SavedPlan; changes: PlanChanges }) {
  if (!plan.last_change) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm">
      <span className="text-fg-secondary">Last change: <span className="text-fg">{plan.last_change}</span></span>
      <Button size="sm" variant="ghost" loading={changes.undoing} onClick={changes.undo}>
        <Undo2 aria-hidden className="size-4" /> Undo last change
      </Button>
    </div>
  );
}
