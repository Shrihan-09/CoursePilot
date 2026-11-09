"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, Badge, Progress, Skeleton } from "@/components/ui/feedback";
import { Input, Select } from "@/components/ui/field";
import { Dialog, useToast } from "@/components/ui/overlay";
import { useAssistantPanel } from "@/features/assistant/assistant-panel";
import { WeekCalendar } from "@/features/schedule/week-calendar";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { deliveryText, termLabel } from "@/lib/academic/vocabulary";
import type { SavedPlan } from "@/types/plan";
import type { BrowserCourse, BrowserRequest } from "@/types/semester-builder";
import type { SchedulePreferences } from "@/types/schedule";
import { gateText, type PlanChanges } from "./plan-changes";

type Picked = { course: string; title: string; credits: string | null; workload_weight: string | null; locked: boolean; pinExplanation?: string };

export function SemesterBuilder({ term, plan, changes, onClose }: {
  term: string | null; plan: SavedPlan; changes: PlanChanges; onClose: () => void;
}) {
  // Mount a fresh draft for each term/revision; never overwrite a draft after a refetch.
  if (!term) return null;
  return <Builder key={`${term}:${plan.revision}`} term={term} plan={plan} changes={changes} onClose={onClose} />;
}

function Builder({ term, plan, changes, onClose }: { term: string; plan: SavedPlan; changes: PlanChanges; onClose: () => void }) {
  const initial = plan.result.terms.find((t) => t.term_code === term)!;
  const panel = useAssistantPanel();
  const latestPanel = useRef(panel);
  latestPanel.current = panel;
  const toast = useToast();
  const [selection, setSelection] = useState<Picked[]>(() => initial.courses.map((c) => ({ course: c.course, title: c.title, credits: c.credits,
    workload_weight: c.workload?.weight ?? null, locked: c.role === "anchor" || (plan.course_states?.some((s) => s.course === c.course && s.term_code === term && s.state === "locked") ?? false) })));
  const [query, setQuery] = useState("");
  const [request, setRequest] = useState<BrowserRequest>({ term_code: term, still_needed: true, offset: 0, limit: 30 });
  const [sectionRank, setSectionRank] = useState(0);
  const [pane, setPane] = useState("browse");
  const selectionCodes = selection.map((c) => c.course);
  const browser = useQuery({ queryKey: ["semester-browser", request, plan.revision, selectionCodes], queryFn: () => api.semesterCourses({ ...request, selection: selectionCodes }), staleTime: 30_000 });
  const profile = useQuery({ queryKey: ["planning-profile"], queryFn: api.planningProfile });
  const calendar = useMutation({ mutationFn: api.schedules });
  const credits = selection.reduce((n, c) => n + Number(c.credits ?? 0), 0);
  const workload = selection.reduce((n, c) => n + Number(c.workload_weight ?? 0), 0);
  const unknownWorkload = selection.some((c) => c.workload_weight == null);
  const update = (patch: Partial<BrowserRequest>) => setRequest((r) => ({ ...r, ...patch, offset: 0 }));
  const filter = (key: string, value: unknown) => update({ filters: { ...request.filters, [key]: value } });
  const add = (c: BrowserCourse) => {
    if ((!c.can_add && !c.can_pin) || selection.some((s) => s.course === c.course)) return;
    let pinExplanation: string | undefined;
    if (c.can_pin && !c.can_add) {
      pinExplanation = window.prompt(`Pin ${c.title}? ${gateText(c.gate ?? "")}. Explain your permission or reason (3?300 characters). The preview must still validate this pin.`)?.trim();
      if (!pinExplanation || pinExplanation.length < 3 || pinExplanation.length > 300) return;
    }
    setSelection((prev) => prev.some((s) => s.course === c.course) ? prev : [...prev, { course: c.course, title: c.title, credits: c.credits, workload_weight: c.workload_weight, locked: false, pinExplanation }]);
    calendar.reset();
  };
  const addSuggestion = async (course: string) => {
    try {
      if (selection.some((c) => c.course === course)) { toast("That course is already in your draft."); return; }
      const result = await api.semesterCourses({ term_code: term, selection: selectionCodes, text: course, still_needed: false, limit: 100 });
      const c = result.items.find((x) => x.course === course);
      if (!c?.can_add) { toast(c?.gate ? gateText(c.gate) : "That suggestion needs review in the course browser.", "danger"); return; }
      add(c); toast(`Added ${c.title} to your draft.`); panel?.close();
    } catch { toast("Couldn't validate that suggestion. Try it in the course browser.", "danger"); }
  };
  // Only update builder context while it is attached; removing its chip detaches it.
  useEffect(() => {
    panel?.setContext({ term, builderTerm: term, selection: selectionCodes, onAddCourse: addSuggestion });
    // Attach once without opening the panel; Ctrl+/ also sees this draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [term]);
  useEffect(() => {
    if (panel?.context?.builderTerm === term) panel.setContext({ ...panel.context, selection: selection.map((s) => s.course), onAddCourse: addSuggestion });
    // Selection, rather than panel object identity, drives the update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection]);
  useEffect(() => () => { const p = latestPanel.current; if (p?.context?.builderTerm === term) p.setContext(null); }, [term]);
  const close = () => { if (panel?.context?.builderTerm === term) panel.setContext(null); onClose(); };
  const ask = () => panel?.open({ term, builderTerm: term, selection: selection.map((s) => s.course), onAddCourse: addSuggestion, draft: "Recommend something for my last slot" });
  const generate = () => {
    const p = profile.data?.profile;
    const s = p?.schedule;
    const preferences: SchedulePreferences = {
      earliest_start: s?.earliest_start ?? null, latest_end: s?.latest_end ?? null, avoid_days: s?.avoid_days ?? [],
      minimum_minutes_between_classes: 0, preferred_campuses: s?.preferred_campuses ?? [],
      availability: "prefer_open", honors: p?.honors ?? null,
      blocked_times: p?.term_overrides?.find((o) => o.term_code === term)?.blocked_times ?? s?.blocked_times ?? [],
    };
    setSectionRank(0); calendar.mutate({ term, courses: selection.map((c) => c.course), preferences, max_results: 10 });
  };
  const results = browser.data;
  return <Dialog open onClose={close} title={`Build ${termLabel(term)}`} description="Browse every course offered this term. Your draft is saved only after Preview and Confirm."
    className="h-[95dvh] max-h-[95dvh] w-[calc(100vw-1rem)] max-w-[100rem] [&[open]]:flex [&[open]]:flex-col" bodyClassName="min-h-0 flex-1 overflow-y-auto" footer={<><Button variant="ghost" onClick={close}>Cancel draft</Button><Button variant="primary" disabled={selection.length > 24} loading={changes.previewing} onClick={() => { changes.request({ kind: "set_term", term_code: term, courses: selection.map((c) => c.course), expected_revision: plan.revision, pin_explanations: Object.fromEntries(selection.filter((c) => c.pinExplanation).map((c) => [c.course, c.pinExplanation!])) }); close(); }}>Preview semester change</Button></>}>
    <div role="group" aria-label="Builder views" className="sticky top-0 z-10 flex flex-wrap gap-1 bg-surface-elevated pb-2 lg:hidden">{[["browse", "Browse"], ["selection", `Selection (${selection.length})`], ["calendar", "Calendar"]].map(([key, label]) => <Button key={key} size="sm" aria-pressed={pane === key} variant={pane === key ? "primary" : "secondary"} onClick={() => setPane(key!)}>{label}</Button>)}</div>
    <div className="grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,0.8fr)_minmax(0,1fr)]" data-testid="semester-builder">
      <section className={cn("min-w-0 space-y-3 lg:block lg:max-h-[calc(95dvh-14rem)] lg:overflow-y-auto lg:pr-2", pane !== "browse" && "hidden")} aria-label="Course browser">
        <h3 className="font-semibold">Course browser</h3>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); update({ text: query }); }}><Input aria-label="Search all term courses" placeholder="Title, code, subject, instructor or topic" value={query} onChange={(e) => setQuery(e.target.value)} /><Button type="submit">Search</Button></form>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="text-xs">What it fills<Select value={request.degree_credit_only ? "@credit" : request.requirements?.[0]?.[0] ?? ""} onChange={(e) => update({ degree_credit_only: e.target.value === "@credit", requirements: e.target.value && e.target.value !== "@credit" ? [[e.target.value]] : undefined })}><option value="">Any course</option><option value="@major">My major</option><option value="@core">School Core / gen-ed</option><option value="@credit">Any degree credit</option><option value="@degree">Any mapped degree requirement</option>{results?.requirements.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}</Select></label>
          <label className="text-xs">Delivery<Select aria-label="Delivery" onChange={(e) => update({ delivery: e.target.value === "hybrid" ? "hybrid" : "any", filters: { ...request.filters, online: e.target.value === "online", async_only: e.target.value === "async", in_person: e.target.value === "in_person" } })}><option value="any">Any delivery</option><option value="async">Async</option><option value="online">Online</option><option value="hybrid">Hybrid</option><option value="in_person">In person</option></Select></label>
          <label className="text-xs">Campus<Input placeholder="e.g. CAC, BUS, LIV" value={request.filters?.campus ?? ""} onChange={(e) => filter("campus", e.target.value || undefined)} /></label>
          <label className="text-xs">Sort<Select value={request.sort ?? "recommended"} onChange={(e) => update({ sort: e.target.value as BrowserRequest["sort"] })}><option value="recommended">Recommended first</option><option value="name">Name</option><option value="credits">Credits</option><option value="fills">What it fills</option></Select></label>
          <label className="text-xs">No earlier than<Input type="time" onChange={(e) => filter("earliest", e.target.value || undefined)} /></label>
          <label className="text-xs">No later than<Input type="time" onChange={(e) => filter("latest", e.target.value || undefined)} /></label>
          <label className="text-xs">Level<Select onChange={(e) => filter("level", Number(e.target.value) || undefined)}><option value="">Any level</option>{[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}00 level</option>)}</Select></label>
          <label className="text-xs">Credits<Input type="number" min={1} max={30} onChange={(e) => filter("credits", Number(e.target.value) || undefined)} /></label>
        </div>
        <fieldset className="flex flex-wrap gap-2 text-xs"><legend>Avoid days</legend>{["M", "T", "W", "H", "F", "S", "U"].map((d) => <label key={d}><input type="checkbox" checked={request.filters?.avoid_days?.includes(d) ?? false} onChange={(e) => filter("avoid_days", e.target.checked ? [...(request.filters?.avoid_days ?? []), d] : request.filters?.avoid_days?.filter((x) => x !== d))} /> {d}</label>)}</fieldset>
        <div className="flex flex-wrap gap-3 text-xs"><label><input type="checkbox" checked={request.still_needed} onChange={(e) => update({ still_needed: e.target.checked })} /> Still-needed only</label><label><input type="checkbox" checked={request.filters?.open_only ?? false} onChange={(e) => filter("open_only", e.target.checked)} /> Open sections only</label></div>
        <p className="text-xs text-fg-muted">Saved blocked times are respected. Turn off Still-needed to explore courses outside your remaining requirements. Requirement coverage is limited to current reviewed data.</p>
        {browser.isPending && <Skeleton className="h-32" />}
        {browser.isError && <Alert tone="danger" title="Course browser couldn't load"><Button onClick={() => void browser.refetch()}>Retry</Button></Alert>}
        {results && <><p className="text-xs text-fg-secondary" role="status">{results.total_offered} courses offered · {results.matching} match filters: {results.eligible} available, {results.gated} gated, {results.selected} already selected</p>
          {results.interpretation?.notes.map((note) => <p key={note} className="text-xs text-fg-secondary">{note}</p>)}
          {results.interpretation?.did_you_mean.map((d) => <div key={d.phrase} className="space-y-2"><p className="text-sm">Which meaning of “{d.phrase}”?</p><div className="flex flex-wrap gap-1">{d.options.map((o) => <Button key={o.requirements.join()} size="sm" variant="secondary" onClick={() => update({ requirements: [o.requirements] })}>{o.names.join(" / ")}</Button>)}</div></div>)}
          {!results.total_offered && <Alert tone="info" title="SOC data is not available for this term">Nothing is inferred from a planner shortlist. Try again once the term data is loaded.</Alert>}
          {results.matching === 0 && results.total_offered > 0 && <p className="text-sm">No matches. Broaden the filters or turn off Still-needed only.</p>}
          <ul className="grid gap-2">{results.items.map((c) => <li key={c.course} className="min-w-0 space-y-2 rounded-lg border border-border p-3" data-testid="browser-course"><h4 className="font-semibold [overflow-wrap:anywhere]">{c.title}</h4><p className="text-xs text-fg-muted">{c.course} · {c.credits ?? "Variable"} credits · {deliveryText(c.delivery)} · {c.open_status}</p><div className="flex flex-wrap gap-1">{c.recommended && <Badge tone="info">Recommended</Badge>}<span className="text-xs">{c.coverage_label}</span></div><p className="text-xs text-fg-secondary">{c.gate ? gateText(c.gate) : c.prerequisite?.state === "satisfied_by_history" ? "Prerequisites met" : c.prerequisite?.state === "conditional_on_plan" ? "Met if earlier planned courses pass" : "Prerequisites need review"}</p>
            <details className="text-xs text-fg-secondary"><summary className="cursor-pointer">Details</summary><p>{c.prerequisite?.raw_text?.replace(/<[^>]+>/g, "")}</p>{c.prerequisite?.conditional_on.map((d) => <p key={d.course}>Needs {d.course} in {termLabel(d.term_code)}</p>)}{c.similar_to && <p>{c.similar_to}</p>}{c.gate && <p>{gateText(c.gate)}</p>}</details>
            <Button size="sm" variant="secondary" disabled={selection.some((s) => s.course === c.course) || !c.can_add} onClick={() => add(c)}>{selection.some((s) => s.course === c.course) ? "Selected" : "Add"}</Button>{c.can_pin && !c.can_add && <Button size="sm" variant="ghost" onClick={() => add(c)}>Preview as a pin</Button>}
          </li>)}</ul>
          <div className="flex justify-between gap-2"><Button size="sm" disabled={!request.offset} onClick={() => setRequest({ ...request, offset: Math.max(0, (request.offset ?? 0) - 30) })}>Previous</Button><Button size="sm" disabled={(request.offset ?? 0) + 30 >= results.matching} onClick={() => setRequest({ ...request, offset: (request.offset ?? 0) + 30 })}>Next</Button></div>
        </>}
      </section>
      <section className={cn("min-w-0 space-y-3 lg:block lg:max-h-[calc(95dvh-14rem)] lg:overflow-y-auto lg:pr-2", pane !== "selection" && "hidden")} aria-label="Semester selection"><h3 className="font-semibold">This semester</h3><p className="text-sm" role="status">{credits} credits · workload {workload}{unknownWorkload ? " + unknown" : ""}</p><p className="text-xs text-fg-muted">Credit range {initial.workload?.credit_floor ?? "—"}–{initial.workload?.credit_max ?? plan.constraints.max_credits_per_term}</p><Progress value={unknownWorkload ? null : workload} max={Number(initial.workload?.budget ?? 1)} label={`Workload ${workload} against ${initial.workload?.budget ?? "unknown"} budget`} />
        {credits > Number(initial.workload?.credit_max ?? plan.constraints.max_credits_per_term) && <Alert tone="warning" title="Over your credit maximum">Remove a course before saving; the preview checks all limits.</Alert>}
        <ul className="space-y-2">{selection.map((c) => <li key={c.course} className="rounded border border-border p-2 text-sm"><p className="font-medium [overflow-wrap:anywhere]">{c.title}</p><p className="text-xs text-fg-muted">{c.course} · {c.credits ?? "Variable"} credits{c.locked ? " · Locked" : ""}</p><Button size="sm" variant="ghost" disabled={c.locked} onClick={() => { setSelection(selection.filter((s) => s.course !== c.course)); calendar.reset(); }}>Remove</Button></li>)}</ul>
        <Button variant="secondary" className="h-auto whitespace-normal" onClick={ask}>Ask CoursePilot about my last slot</Button><p className="text-xs text-fg-muted">Suggestions add to this draft. CoursePilot never replaces a selection automatically.</p>
      </section>
      <section className={cn("min-w-0 space-y-3 lg:block lg:max-h-[calc(95dvh-14rem)] lg:overflow-y-auto lg:pr-2", pane !== "calendar" && "hidden")} aria-label="Weekly preview"><h3 className="font-semibold">Weekly preview</h3><Button disabled={!selection.length || selection.length > 8 || profile.isPending || profile.isError} loading={calendar.isPending} onClick={generate}>Auto-pick best sections</Button>
        {selection.length > 8 && <p className="text-xs text-warning">The existing schedule engine supports up to 8 courses at once.</p>}
        {calendar.isError && <Alert tone="danger" title="Schedule preview failed">Try again or compare schedules.</Alert>}
        {calendar.data?.issues.map((i) => <p className="text-sm text-warning" key={i.code + i.message}>{i.message}</p>)}
        {calendar.data && !calendar.data.options.length && <Alert tone="warning" title="No verified schedule">Review conflicts, restrictions, blocked times and campus travel before saving.</Alert>}
        {!!calendar.data?.options.length && <><label className="block text-xs">Section combination<Select value={sectionRank} onChange={(e) => setSectionRank(Number(e.target.value))}>{calendar.data.options.map((o, i) => <option key={o.rank} value={i}>Option {o.rank}: {o.choices.map((c) => `${c.course} ${c.section_number}`).join(" · ")}</option>)}</Select></label><WeekCalendar option={calendar.data.options[sectionRank] ?? calendar.data.options[0]!} />{calendar.data.options[sectionRank]?.choices.map((c) => <p key={c.index_number} className="text-xs">{c.title ?? c.course} · section {c.section_number} · {c.meetings.map((m) => m.campus_name ?? m.campus).filter(Boolean).join(", ") || "Campus unknown"}</p>)}</>}
        {!calendar.data && <p className="text-sm text-fg-secondary">Choose courses, then preview sections against your saved time and campus preferences.</p>}
        {selection.length > 0 && selection.length <= 8 && <Link href={`/schedule?term=${term}&courses=${encodeURIComponent(selection.map((c) => c.course).join(","))}`} target="_blank" rel="noopener" className="block text-sm text-primary-text underline">Open schedule options comparison</Link>}<p className="text-xs text-fg-muted">Comparison opens your draft courses in a new tab. Section previews do not register you or save WebReg indexes.</p>
      </section>
    </div>
  </Dialog>;
}
