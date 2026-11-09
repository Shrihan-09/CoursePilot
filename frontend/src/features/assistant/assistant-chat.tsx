"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Copy, ExternalLink, MessageSquareText, Send, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, Badge, Skeleton } from "@/components/ui/feedback";
import { useToast } from "@/components/ui/overlay";
import { PlanDiffView } from "@/features/plan/plan-changes";
import { chipBody, deliveryWords, OPEN_STATUS, streamMessage } from "@/lib/assistant";
import { ApiError, api } from "@/lib/api";
import { termLabel } from "@/lib/academic/vocabulary";
import { cn } from "@/lib/utils";
import type {
  AssistantReply,
  Chip,
  HistoryMessage,
  MessageBody,
  OptionCard,
  ProposalCard,
  Question,
  SectionCard,
} from "@/types/assistant";
import type { PlanDiff } from "@/types/plan";

/**
 * Prompt 11 Part B: the assistant's chat panel.
 *
 * Nothing here applies a change by itself. A proposal applies only on its
 * Confirm; an option card's "Choose this" is the student's confirmation for
 * that one swap (the server asks for one more tap when the swap has a big side
 * effect). Every card is drawn from the server's tool data - the UI never
 * writes a description or a label - and nothing rates a course easy or hard.
 */

type Turn =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; reply: AssistantReply; live?: boolean };

let seq = 0;
const nextId = () => `t${++seq}`;

function fromHistory(history: HistoryMessage[]): Turn[] {
  return history.map((m): Turn => (m.role === "user" || !m.reply
    ? { id: m.id, role: "user", text: m.content }
    : { id: m.id, role: "assistant", reply: m.reply }));
}

/* ------------------------------------------------------------- the panel */

export function AssistantChat({ compact = false, conversationId, contextTerm, draft, onBusy, onDraftUsed }: {
  compact?: boolean; conversationId?: string; contextTerm?: string; draft?: string; onBusy?: (busy: boolean) => void; onDraftUsed?: () => void;
}) {
  const status = useQuery({ queryKey: conversationId ? ["assistant", conversationId] : ["assistant"], queryFn: () => api.assistantStatus(conversationId), staleTime: 30_000 });
  const qc = useQueryClient();
  const toast = useToast();
  // null until this panel adds a turn: until then the server's history is shown.
  const [local, setTurns] = useState<Turn[] | null>(() => qc.getQueryData<Turn[]>(["assistant-turns", conversationId]) ?? null);
  const [activity, setActivity] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [remaining, setRemaining] = useState<number | null>(null);

  const bottom = useRef<HTMLDivElement>(null);
  const history = status.data?.history;
  const turns = local ?? (history ? fromHistory(history) : null);

  useEffect(() => {
    if (local) qc.setQueryData(["assistant-turns", conversationId], local);
  }, [local, qc, conversationId]);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "end", behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }, [local, activity]);

  const push = useCallback((t: Turn) => setTurns((xs) => [...(xs ?? fromHistory(history ?? [])), t]),
                           [history]);

  const undo = useMutation({
    mutationFn: () => api.undoChange(),
    onSuccess: () => {
      toast("Undone. Your plan is back to how it was.", "success");
      void qc.invalidateQueries({ queryKey: ["plan"] });
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : "Nothing to undo.", "danger"),
  });

  const applied = useCallback((reply: AssistantReply) => {
    push({ id: nextId(), role: "assistant", reply, live: true });
    if (reply.undo_available) {
      toast(reply.applied_summary ?? "Your plan was updated.", "success",
            { label: "Undo", onClick: () => undo.mutate() });
      void qc.invalidateQueries({ queryKey: ["plan"] });
    }
  }, [push, qc, toast, undo]);

  const send = useCallback(async (body: MessageBody, shown: string) => {
    if (pending) return;
    if (shown) push({ id: nextId(), role: "user", text: shown });
    if (body.text) onDraftUsed?.();
    setPending(true);
    setActivity([]);
    try {
      await streamMessage({ ...body, conversation_id: conversationId, context_term: contextTerm }, (ev) => {
        if (ev.type === "activity") setActivity((xs) => (xs.includes(ev.label) ? xs : [...xs, ev.label]));
        else if (ev.type === "reply") {
          push({ id: nextId(), role: "assistant", reply: ev.reply, live: true });
          setRemaining(ev.remaining_today);
        } else toast(ev.detail, "danger");
      });
    } catch {
      toast("The assistant is unreachable. Try again.", "danger");
    } finally {
      setPending(false);
      setActivity([]);
      void qc.invalidateQueries({ queryKey: ["assistant"] });
    }
  }, [pending, push, toast, qc, conversationId, contextTerm, onDraftUsed]);

  const tap = useMutation({
    mutationFn: (token: string) => api.assistantAction(token),
    onSuccess: ({ reply }) => { applied(reply); void qc.invalidateQueries({ queryKey: ["assistant"] }); },
    onError: (e) => toast(e instanceof ApiError ? e.message : "That didn't work. Try again.", "danger"),
  });

  useEffect(() => { onBusy?.(pending || tap.isPending); }, [pending, onBusy, tap.isPending]);

  const onChip = (chip: Chip) => send(chipBody(chip), chip.question ? chip.label : chip.label || chip.value);

  if (status.isLoading) {
    return <div className="space-y-3 p-4"><Skeleton className="h-16" /><Skeleton className="h-24" /></div>;
  }
  if (status.error || !status.data) {
    return <Alert tone="danger" title="The assistant didn't load">Try again in a moment.</Alert>;
  }
  if (turns === null) return null;
  const s = status.data;
  const ctx: Ctx = { send, onChip, tap: (t) => tap.mutate(t), tapping: tap.isPending, pending };

  return (
    <div className={cn("flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden break-words", compact ? "" : "min-h-0")}>
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2 text-xs text-fg-secondary">
        <span>{remaining ?? s.remaining_today} of {s.daily_limit} messages left today</span>

      </div>

      <div role="log" aria-live="polite" aria-label="Conversation"
           className="min-h-0 flex-1 space-y-4 overflow-y-auto px-3 py-4">
        {!s.enabled && <Alert tone="info" title="Assistant unavailable">{s.reason}</Alert>}
        {turns.length === 0 && s.enabled && <Welcome suggestions={s.suggestions} onChip={onChip} />}
        {turns.map((t) => (t.role === "user"
          ? <UserBubble key={t.id} text={t.text} />
          : <AssistantMessage key={t.id} reply={t.reply} live={!!t.live} ctx={ctx} />))}
        {pending && <Working labels={activity} />}
        <div ref={bottom} />
      </div>

      <Composer draft={draft} disabled={!s.enabled || pending || (remaining ?? s.remaining_today) <= 0}
                onSend={(text) => send({ text }, text)} />
      <p className="px-3 pb-2 text-[11px] leading-snug text-fg-muted">{s.footer}</p>


    </div>
  );
}

interface Ctx {
  send: (body: MessageBody, shown: string) => void;
  onChip: (chip: Chip) => void;
  tap: (token: string) => void;
  tapping: boolean;
  pending: boolean;
}

/* ------------------------------------------------------------- messages */

function Welcome({ suggestions, onChip }: { suggestions: Chip[]; onChip: (c: Chip) => void }) {
  return (
    <div className="space-y-3 rounded border border-border bg-surface p-4 text-sm">
      <p className="flex items-center gap-2 font-medium text-fg">
        <Sparkles aria-hidden className="size-4 text-primary-text" /> Ask about your plan, your progress, or a course.
      </p>
      <p className="text-fg-secondary">
        I answer from your records and plan, and I only change your plan when you confirm.
      </p>
      <ChipRow chips={suggestions} onChip={onChip} />
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">{text}</p>
    </div>
  );
}

/** Reveals an already-validated reply progressively (never unvalidated text). */
function useReveal(text: string, live: boolean) {
  const [n, setN] = useState(live ? 0 : text.length);
  useEffect(() => {
    if (!live) return;
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { setN(text.length); return; }
    const timer = window.setInterval(() => setN((x) => {
      if (x >= text.length) { window.clearInterval(timer); return x; }
      return Math.min(text.length, x + 6);
    }), 16);
    return () => window.clearInterval(timer);
  }, [text, live]);
  return text.slice(0, n);
}

function AssistantMessage({ reply, live, ctx }: { reply: AssistantReply; live: boolean; ctx: Ctx }) {
  const shown = useReveal(reply.text, live);
  const done = shown.length >= reply.text.length;
  return (
    <div className="space-y-3" data-testid="assistant-message">
      <ActivityLine labels={reply.activity.map((a) => a.label)} />
      <div className="max-w-[95%] rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg">
        <p className="whitespace-pre-wrap">{shown}</p>
      </div>
      {done && (
        <>
          {reply.questions.length > 0 && <Questions questions={reply.questions} ctx={ctx} />}
          {reply.cards.length > 0 && <OptionCarousel cards={reply.cards} ctx={ctx} />}
          {reply.proposals.map((p, i) => <Proposal key={`${p.preview_id}-${i}`} p={p} ctx={ctx} />)}
          {reply.sections.length > 0 && <Sections sections={reply.sections} />}
          {reply.webreg_term && <WebRegPanelView term={reply.webreg_term} />}
          {reply.suggestions.length > 0 && <ChipRow chips={reply.suggestions} onChip={ctx.onChip} disabled={ctx.pending} />}
        </>
      )}
    </div>
  );
}

/** "Checked your plan · Found 6 options" - collapsible. */
export function ActivityLine({ labels }: { labels: string[] }) {
  if (!labels.length) return null;
  return (
    <details className="group text-xs text-fg-muted">
      <summary className="flex cursor-pointer list-none items-center gap-1 [&::-webkit-details-marker]:hidden">
        <ChevronDown aria-hidden className="size-3 transition group-open:rotate-180" />
        {labels.slice(0, 2).join(" · ")}{labels.length > 2 ? ` · +${labels.length - 2}` : ""}
      </summary>
      <ul className="mt-1 space-y-0.5 pl-4">{labels.map((l) => <li key={l}>{l}</li>)}</ul>
    </details>
  );
}

function Working({ labels }: { labels: string[] }) {
  return (
    <p className="flex items-center gap-2 text-xs text-fg-muted" role="status">
      <span aria-hidden className="size-2 animate-pulse rounded-full bg-primary" />
      {labels.length ? labels.join(" · ") : "Thinking…"}
    </p>
  );
}

export function ChipRow({ chips, onChip, disabled }: { chips: Chip[]; onChip: (c: Chip) => void; disabled?: boolean }) {
  return (
    <div className="flex flex-wrap gap-2">
      {chips.map((c) => (
        <button key={`${c.question ?? ""}-${c.value}`} type="button" disabled={disabled}
                onClick={() => onChip(c)}
                className="rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-fg hover:border-primary disabled:opacity-50">
          {c.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------------------- clarify (quick replies) */

/** 1-3 questions. One question: a tap answers it. Several: pick, then "Show options". */
export function Questions({ questions, ctx }: { questions: Question[]; ctx: Pick<Ctx, "send" | "onChip" | "pending"> }) {
  const [picked, setPicked] = useState<Record<string, Chip>>({});
  if (questions.length === 1) {
    const q = questions[0]!;
    return (
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-fg">{q.prompt}</legend>
        <ChipRow chips={q.chips} onChip={ctx.onChip} disabled={ctx.pending} />
      </fieldset>
    );
  }
  const submit = () => {
    const answers = Object.fromEntries(Object.entries(picked).map(([k, c]) => [k, c.value]));
    const shown = Object.values(picked).map((c) => c.label).join(" · ") || "Show options";
    ctx.send({ answers: Object.keys(answers).length ? answers : { else: "nothing" } }, shown);
  };
  return (
    <div className="space-y-3 rounded border border-border p-3">
      {questions.map((q) => (
        <fieldset key={q.id} className="space-y-2">
          <legend className="text-sm font-medium text-fg">{q.prompt}</legend>
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={q.prompt}>
            {q.chips.map((c) => {
              const on = picked[q.id]?.value === c.value;
              return (
                <button key={c.value} type="button" role="radio" aria-checked={on} disabled={ctx.pending}
                        onClick={() => setPicked((p) => ({ ...p, [q.id]: c }))}
                        className={cn("rounded-full border px-3 py-1 text-xs font-medium",
                                      on ? "border-primary bg-primary text-primary-foreground" : "border-border bg-surface text-fg")}>
                  {c.label}
                </button>
              );
            })}
          </div>
        </fieldset>
      ))}
      <Button size="sm" variant="primary" disabled={ctx.pending} onClick={submit}>Show options</Button>
      <p className="text-xs text-fg-muted">Or just type what you want.</p>
    </div>
  );
}

/* ---------------------------------------------------------- the carousel */

export function OptionCarousel({ cards, ctx }: { cards: OptionCard[]; ctx: Pick<Ctx, "tap" | "tapping" | "onChip" | "pending"> }) {
  return (
    <div className="-mx-3 overflow-x-auto px-3 pb-2" role="region" aria-label="Course options">
      <ul className="flex snap-x snap-mandatory gap-3">
        {cards.map((c) => <OptionCardView key={c.course} card={c} ctx={ctx} />)}
      </ul>
    </div>
  );
}

function OptionCardView({ card: c, ctx }: { card: OptionCard; ctx: Pick<Ctx, "tap" | "tapping" | "onChip" | "pending"> }) {
  const hours = c.contact_hours != null ? `${Number(c.contact_hours)} h/week` : null;
  const weight = c.workload_weight != null ? `workload weight ${Number(c.workload_weight)}` : null;
  return (
    <li className="flex w-[17rem] shrink-0 snap-start flex-col gap-2 rounded-lg border border-border bg-surface p-3 text-sm"
        data-testid="option-card">
      <div>
        <p className="code text-xs text-fg-secondary">{c.course}{c.credits != null && ` · ${Number(c.credits)} cr`}</p>
        <h4 className="font-semibold leading-snug text-fg">{c.title}</h4>
      </div>
      {c.description
        ? <p className="line-clamp-3 text-xs text-fg-secondary" title={`From the ${c.description_source ?? "catalog"}`}>{c.description}</p>
        : <p className="text-xs italic text-fg-muted">No catalog description available.</p>}
      {c.why.length > 0 && (
        <p className="text-xs text-fg"><span className="font-medium">Why it works for you: </span>{c.why.join(" · ")}</p>
      )}
      <p className="text-xs text-fg-secondary">
        {[deliveryWords(c.delivery), weight, hours].filter(Boolean).join(" · ")}
      </p>
      <div className="flex flex-wrap gap-1">
        {c.offering && <Badge tone={c.offering === "confirmed" ? "success" : "neutral"}>{c.offering}{c.term_code ? ` · ${termLabel(c.term_code)}` : ""}</Badge>}
        {c.open_status && <Badge tone={c.open_status === "closed" ? "warning" : "neutral"}>{OPEN_STATUS[c.open_status] ?? c.open_status}</Badge>}
      </div>
      <div className="mt-auto flex gap-2 pt-1">
        {c.action_token && (
          <Button size="sm" variant="primary" loading={ctx.tapping} onClick={() => ctx.tap(c.action_token!)}>
            Choose this
          </Button>
        )}
        <Button size="sm" variant="ghost" disabled={ctx.pending}
                onClick={() => ctx.onChip({ label: `More like ${c.course}`, value: `more_like:${c.course}` })}>
          More like this
        </Button>
      </div>
    </li>
  );
}

/* ------------------------------------------------------------ proposals */

function isDiff(d: ProposalCard["diff"]): d is PlanDiff {
  return Array.isArray((d as PlanDiff).terms);
}

export function Proposal({ p, ctx }: { p: ProposalCard; ctx: Pick<Ctx, "tap" | "tapping"> }) {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return <p className="text-xs text-fg-muted">Not now: {p.title}. Nothing changed.</p>;
  return (
    <section className="space-y-3 rounded-lg border border-border bg-surface p-3" data-testid="proposal-card"
             aria-label={p.title}>
      <h4 className="font-semibold text-fg">{p.title}</h4>
      {p.reason === "second_tap" && (
        <Alert tone="warning" title="This choice affects more of your plan">Review what else changes, then confirm.</Alert>
      )}
      {p.reason === "refreshed" && (
        <Alert tone="info" title="Your plan changed since this preview">This is the change's effect now.</Alert>
      )}
      {isDiff(p.diff) ? <PlanDiffView diff={p.diff} /> : <p className="text-sm text-fg">{p.summary}</p>}
      <p className="text-xs text-fg-muted">Nothing changes until you confirm.</p>
      <div className="flex gap-2">
        <Button size="sm" variant="ghost" onClick={() => setDismissed(true)}>Not now</Button>
        {p.action_token
          ? <Button size="sm" variant="primary" loading={ctx.tapping} onClick={() => ctx.tap(p.action_token!)}>Confirm</Button>
          : <span className="text-xs text-fg-muted">Ask again to get a fresh preview.</span>}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------- sections */

function Sections({ sections }: { sections: SectionCard[] }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [chosen, setChosen] = useState<string | null>(null);
  const choose = useMutation({
    mutationFn: (s: SectionCard) => api.choosePlannedSection(s.term_code, s.course, s.index_number),
    onSuccess: (_, s) => {
      setChosen(s.index_number);
      toast(`Section ${s.section_number} (#${s.index_number}) saved to ${termLabel(s.term_code)}.`, "success");
      void qc.invalidateQueries({ queryKey: ["webreg", s.term_code] });
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : "Couldn't save that section.", "danger"),
  });
  return (
    <ul className="space-y-2" aria-label="Sections that fit your week">
      {sections.map((s) => (
        <li key={s.index_number} className="space-y-1 rounded border border-border p-3 text-sm" data-testid="section-card">
          <p className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="font-medium text-fg">Section {s.section_number} · index <span className="code">{s.index_number}</span></span>
            <Badge tone={s.open_status === "open" ? "success" : s.open_status === "closed" ? "warning" : "neutral"}>
              {OPEN_STATUS[s.open_status] ?? s.open_status}
            </Badge>
          </p>
          <p className="text-xs text-fg-secondary">{s.meetings.join(" · ") || "Time not published"}</p>
          <p className="text-xs text-fg-secondary">{[s.campus, s.instructors.join(", ")].filter(Boolean).join(" · ")}</p>
          {s.warnings.map((w) => <p key={w} className="text-xs text-warning">{w}</p>)}
          <Button size="sm" variant={chosen === s.index_number ? "secondary" : "primary"}
                  loading={choose.isPending && choose.variables?.index_number === s.index_number}
                  disabled={chosen === s.index_number} onClick={() => choose.mutate(s)}>
            {chosen === s.index_number ? "Saved" : "Use this section"}
          </Button>
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------- Ready for WebReg (B1c) */

export function WebRegPanelView({ term }: { term: string }) {
  const toast = useToast();
  const panel = useQuery({ queryKey: ["webreg", term], queryFn: () => api.webregPanel(term) });
  if (panel.isLoading) return <Skeleton className="h-24" />;
  if (!panel.data) return null;
  const p = panel.data;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(p.index_numbers.join(", "));
      toast(`Copied ${p.index_numbers.length} index number${p.index_numbers.length === 1 ? "" : "s"}.`, "success");
    } catch {
      toast("Couldn't copy - select the numbers and copy them yourself.", "danger");
    }
  };
  return (
    <section className="space-y-3 rounded-lg border border-border bg-surface p-3 text-sm" aria-label="Ready for WebReg"
             data-testid="webreg-panel">
      <h4 className="font-semibold text-fg">Ready for WebReg · {p.label}</h4>
      {p.items.length === 0
        ? <p className="text-fg-secondary">No sections chosen for {p.label} yet. Ask me to pick sections that fit your week.</p>
        : (
          <ul className="space-y-1">
            {p.items.map((i) => (
              <li key={i.course}>
                <span className="code font-semibold text-fg">{i.index_number}</span>{" "}
                <span className="code text-fg-secondary">{i.course}</span> {i.title}
                {i.section_number && <span className="text-fg-secondary"> · section {i.section_number}</span>}
                {i.warnings.map((w) => <span key={w} className="block text-xs text-warning">{w}</span>)}
              </li>
            ))}
          </ul>
        )}
      {p.warnings.map((w) => <p key={w} className="text-xs text-warning">Time conflict: {w}</p>)}
      {p.without_section.length > 0 && (
        <p className="text-xs text-fg-muted">No section yet: {p.without_section.map((c) => c.course).join(", ")}</p>
      )}
      {p.items.length > 0 && (
        <Button size="sm" variant="primary" onClick={copy}><Copy aria-hidden className="size-4" /> Copy all index numbers</Button>
      )}
      <ol className="list-decimal space-y-0.5 pl-5 text-xs text-fg-secondary">
        {p.steps.map((s) => <li key={s}>{s}</li>)}
      </ol>
      <a href={p.webreg_url} target="_blank" rel="noreferrer noopener"
         className="inline-flex items-center gap-1 text-xs font-medium text-primary-text underline">
        Open WebReg <ExternalLink aria-hidden className="size-3" />
      </a>
      <p className="text-xs text-fg-muted">{p.note}</p>
    </section>
  );
}

/* ------------------------------------------------------------- composer */

function Composer({ disabled, onSend, draft }: { disabled: boolean; onSend: (text: string) => void; draft?: string }) {
  const [text, setText] = useState(draft ?? "");
  useEffect(() => { if (draft) setText(draft); }, [draft]);
  const submit = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText("");
  };
  return (
    <form className="flex items-end gap-2 border-t border-border px-3 py-2"
          onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <label htmlFor="assistant-input" className="sr-only">Message the assistant</label>
      <textarea id="assistant-input" rows={1} maxLength={600} value={text} disabled={disabled}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
                placeholder="Ask about your plan, a course, or a semester"
                className="min-h-10 min-w-0 flex-1 resize-none rounded border border-border bg-surface px-3 py-2 text-sm text-fg placeholder:text-fg-muted" />
      <Button type="submit" variant="primary" disabled={disabled || !text.trim()} aria-label="Send">
        <Send aria-hidden className="size-4" />
      </Button>
    </form>
  );
}

/* --------------------------------------------- the Plan page's button */

export function AssistantLauncher({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen} aria-label="Open the assistant"
            className="fixed bottom-24 right-4 z-40 inline-flex items-center gap-2 rounded-full bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground shadow-elevated sm:bottom-6">
      <MessageSquareText aria-hidden className="size-5" /> Ask
    </button>
  );
}
