/**
 * Prompt 11 Part B: the assistant panel - stream parsing, quick replies, the
 * option carousel, proposal cards, sections and the WebReg hand-off.
 * Fabricated data; rendered to static markup like the rest of the suite.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ToastProvider } from "@/components/ui/overlay";
import {
  ActivityLine,
  AssistantChat,
  Composer,
  OptionCarousel,
  Proposal,
  Questions,
  WebRegPanelView,
} from "@/features/assistant/assistant-chat";
import { ASSISTANT_MAX_INPUT_CHARS, chipBody, parseNdjson, streamMessage } from "@/lib/assistant";
import type { AssistantReply, AssistantStatus, OptionCard, ProposalCard, StreamEvent } from "@/types/assistant";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;|&apos;/g, "'").replace(/&quot;/g, '"')
  .replace(/&amp;/g, "&").replace(/\s+/g, " ");
const EASY_HARD = /\b(easy|easier|hard|harder|difficult)\b/i;
const noop = () => {};
const ctx = { send: noop, onChip: noop, tap: noop, tapping: false, pending: false };

function withClient(node: React.ReactNode, seed: (c: QueryClient) => void = () => {}) {
  const client = new QueryClient();
  seed(client);
  return renderToStaticMarkup(
    <QueryClientProvider client={client}><ToastProvider>{node}</ToastProvider></QueryClientProvider>);
}

const card = (over: Partial<OptionCard> = {}): OptionCard => ({
  course: "01:700:101", title: "Introduction to Music", credits: "3", term_code: "20271",
  description: "Listening skills and the history of Western and global music.", description_source: "Rutgers catalog",
  why: ["async", "fills AHp"], requirements: ["Arts and Humanities"], requirement_codes: ["AHp"],
  delivery: ["async"], workload_weight: "0.8", contact_hours: "3", open_status: "open",
  offering: "historically offered", replaces: "01:082:105", action_token: "tok-1234567890abcdef", ...over,
});

const reply = (over: Partial<AssistantReply> = {}): AssistantReply => ({
  text: "Here are 2 options.", intent: "guided_swap", questions: [], cards: [], proposals: [], sections: [],
  suggestions: [], activity: [], webreg_term: null, applied_summary: null, undo_available: false,
  generated_by: "template", footer: "Coursenav is a planning aid; your advisor and Degree Navigator are authoritative.",
  ...over,
});

describe("the stream", () => {
  it("parses NDJSON lines and keeps an unfinished tail", () => {
    const { events, rest } = parseNdjson('{"type":"activity","label":"Checked your plan"}\n{"type":"rep');
    expect(events).toEqual([{ type: "activity", label: "Checked your plan" }]);
    expect(rest).toBe('{"type":"rep');
  });

  it("delivers activity and then the reply from a chunked body", async () => {
    const lines = ['{"type":"activity","label":"Checked your plan"}\n{"type":"act',
                   'ivity","label":"Found 3 options"}\n',
                   `${JSON.stringify({ type: "reply", reply: reply(), remaining_today: 49 })}\n`];
    const body = new ReadableStream({
      start(c) { lines.forEach((l) => c.enqueue(new TextEncoder().encode(l))); c.close(); },
    });
    const seen: StreamEvent[] = [];
    let sent: RequestInit | undefined;
    const fetcher = (async (_url: string, init?: RequestInit) => {
      sent = init;
      return new Response(body, { status: 200 });
    }) as unknown as typeof fetch;
    await streamMessage({ text: "Make spring lighter" }, (e) => seen.push(e), fetcher);
    expect(seen.map((e) => e.type)).toEqual(["activity", "activity", "reply"]);
    expect(JSON.parse(String(sent?.body))).toEqual({ text: "Make spring lighter" });
  });

  it("turns an HTTP error into an error event", async () => {
    const fetcher = (async () => new Response(JSON.stringify({ detail: "The assistant isn't set up." }),
                                              { status: 503 })) as unknown as typeof fetch;
    const seen: StreamEvent[] = [];
    await streamMessage({ text: "hi" }, (e) => seen.push(e), fetcher);
    expect(seen).toEqual([{ type: "error", status: 503, detail: "The assistant isn't set up." }]);
  });

  it("sends a tapped chip as a chip, never as free text", () => {
    expect(chipBody({ label: "Async / online", value: "online", question: "format" }))
      .toEqual({ chip: { label: "Async / online", value: "online", question: "format" } });
  });
});

describe("cards", () => {
  it("an option card shows tool data verbatim, with Choose this and More like this", () => {
    const html = withClient(<OptionCarousel cards={[card(), card({ course: "01:965:211", title: "Theater Appreciation",
      description: null, action_token: null, offering: "confirmed" })]} ctx={ctx} />);
    const t = text(html);
    expect(t).toContain("Introduction to Music");
    expect(t).toContain("Listening skills and the history of Western and global music.");
    expect(t).toContain("Why it works for you: async · fills AHp");
    expect(t).toContain("historically offered · Spring 2027");
    expect(t).toContain("No catalog description available.");
    expect((t.match(/Choose this/g) ?? []).length).toBe(1);      // only the card with a token
    expect((t.match(/More like this/g) ?? []).length).toBe(2);
    expect(html).toContain("snap-x");                             // horizontal carousel
    expect(t).not.toMatch(EASY_HARD);
  });

  it("a proposal shows the diff and applies only on Confirm", () => {
    const p: ProposalCard = {
      title: "Lighten Spring 2027", change: { kind: "lighten_term", term_code: "20271" }, preview_id: "p1",
      summary: "01:082:105 moves to Fall 2027; graduation unchanged", big: false, reason: "second_tap",
      action_token: "tok-abcdefghijklmnop",
      diff: { terms: [{ term_code: "20271", label: "Spring 2027", added: [], removed: ["01:082:105"],
        before: [{ course: "01:082:105", title: "Art History", credits: "3", state: "planner_selected" }], after: [],
        credits_before: "14", credits_after: "11", workload_before: null, workload_after: null, budget: null }],
        forced_moves: [], graduation_before: "20281", graduation_after: "20281", last_term_before: "20281",
        last_term_after: "20281", requirements_lost: [], requirements_gained: [], new_issues: [],
        summary: "01:082:105 moves to Fall 2027; graduation unchanged", big: false },
    } as unknown as ProposalCard;
    const t = text(withClient(<Proposal p={p} ctx={ctx} />));
    expect(t).toContain("Lighten Spring 2027");
    expect(t).toContain("14 → 11 credits");
    expect(t).toContain("Nothing changes until you confirm.");
    expect(t).toContain("This choice affects more of your plan");
    expect(t).toContain("Confirm");
    expect(t).toContain("Not now");
    const stale = text(withClient(<Proposal p={{ ...p, action_token: null, reason: "confirm" }} ctx={ctx} />));
    expect(stale).not.toContain("Confirm ");
    expect(stale).toContain("Ask again to get a fresh preview.");
  });

  it("several clarify questions are answered together; one question is one tap", () => {
    const qs = [{ id: "instead", prompt: "What should it count for instead?", chips: [
      { label: "Same requirement (Arts and Humanities)", value: "same", question: "instead" }] },
      { id: "format", prompt: "Any format you prefer?", chips: [{ label: "Async / online", value: "online", question: "format" }] }];
    const many = text(withClient(<Questions questions={qs} ctx={ctx} />));
    expect(many).toContain("What should it count for instead?");
    expect(many).toContain("Show options");
    expect(many).toContain("Or just type what you want.");
    const one = text(withClient(<Questions questions={[qs[1]!]} ctx={ctx} />));
    expect(one).not.toContain("Show options");
  });

  it("the tool activity line is collapsible", () => {
    const html = withClient(<ActivityLine labels={["Checked your plan", "Found 6 options", "Checked your progress"]} />);
    expect(html).toContain("<details");
    expect(text(html)).toContain("Checked your plan · Found 6 options · +1");
  });
});

describe("Ready for WebReg", () => {
  it("lists index numbers, copy-all, the steps and WebReg's own page - no sign-in", () => {
    const panel = {
      term_code: "20271", label: "Spring 2027", index_numbers: ["17101", "08811"],
      items: [{ course: "01:700:101", index_number: "17101", section_number: "90", title: "Introduction to Music",
                meetings: [], open_status: "closed", warnings: ["Closed — consider a watch"] },
              { course: "01:640:152", index_number: "08811", section_number: "01", title: "Calculus II",
                meetings: ["Mon 10:00am–11:20am"], open_status: "open", warnings: ["Special permission required"] }],
      without_section: [{ course: "01:198:211", title: "Computer Architecture" }], warnings: ["01:640:152 and 01:198:211 overlap"],
      steps: ["Open WebReg", "Choose the term", "Paste the index numbers", "Add to schedule", "Submit"],
      webreg_url: "https://sims.rutgers.edu/webreg/",
      note: "Coursenav does not sign in to WebReg or register you. Copy the index numbers and add them in WebReg yourself.",
    };
    const html = withClient(<WebRegPanelView term="20271" />, (c) => c.setQueryData(["webreg", "20271"], panel));
    const t = text(html);
    expect(t).toContain("Ready for WebReg · Spring 2027");
    expect(t).toContain("17101");
    expect(t).toContain("Copy all index numbers");
    expect(t).toContain("Closed — consider a watch");
    expect(t).toContain("Special permission required");
    expect(t).toContain("Time conflict: 01:640:152 and 01:198:211 overlap");
    expect(t).toContain("No section yet: 01:198:211");
    expect(html).toContain('target="_blank"');
    expect(t).toContain("does not sign in to WebReg");
  });
});

describe("the panel", () => {
  it("renders history, today's allowance and the advising footer", () => {
    const status: AssistantStatus = {
      enabled: true, reason: null, daily_limit: 50, remaining_today: 47, footer: reply().footer,
      suggestions: [{ label: "Make next semester lighter", value: "Make next semester lighter" }],
      history: [
        { id: "1", role: "user", content: "Show online options", reply: null, created_at: null },
        { id: "2", role: "assistant", content: "Here are 2 options.", created_at: null,
          reply: reply({ cards: [card({ action_token: null })], activity: [{ tool: "search_courses", label: "Found 2 courses" }] }) },
      ],
    };
    const t = text(withClient(<AssistantChat />, (c) => c.setQueryData(["assistant"], status)));
    expect(t).toContain("47 of 50 messages left today");
    expect(t).toContain("Show online options");
    expect(t).toContain("Introduction to Music");
    expect(t).toContain("your advisor and Degree Navigator are authoritative");
    expect(t).not.toContain("Choose this");          // history cards carry no token
  });

  it("an unavailable assistant says so instead of failing", () => {
    const status: AssistantStatus = { enabled: false, reason: "The assistant isn't set up on this server yet.",
      daily_limit: 50, remaining_today: 50, suggestions: [], history: [], footer: reply().footer };
    const t = text(withClient(<AssistantChat />, (c) => c.setQueryData(["assistant"], status)));
    expect(t).toContain("The assistant isn't set up on this server yet.");
  });
});


describe("the 250-character input limit", () => {
  // The disabled ATTRIBUTE, not Tailwind's `disabled:` class names.
  const sendDisabled = (html: string) => /\sdisabled=""/.test(html.match(/<button[^>]*aria-label="Send"[^>]*>/)?.[0] ?? "");

  it("matches the backend default", () => {
    expect(ASSISTANT_MAX_INPUT_CHARS).toBe(250);
  });

  it("shows a live counter and allows exactly the limit", () => {
    const html = renderToStaticMarkup(<Composer disabled={false} onSend={noop} draft={"a".repeat(250)} />);
    expect(text(html)).toContain("250/250");
    expect(sendDisabled(html)).toBe(false);
    expect(html).not.toContain('aria-invalid="true"');
  });

  it("disables Send and says how far over past the limit", () => {
    const html = renderToStaticMarkup(<Composer disabled={false} onSend={noop} draft={"a".repeat(251)} />);
    expect(text(html)).toContain("1 over the 250-character limit");
    expect(sendDisabled(html)).toBe(true);
    expect(html).toContain('aria-invalid="true"');
    // No silent truncation: the textarea has no hard maxLength.
    expect(html).not.toMatch(/maxlength=/i);
  });

  it("does not count surrounding whitespace, like the API", () => {
    const html = renderToStaticMarkup(<Composer disabled={false} onSend={noop} draft={`  ${"a".repeat(250)}  `} />);
    expect(text(html)).toContain("250/250");
  });

  it("uses the server's limit when the status sends one", () => {
    const html = renderToStaticMarkup(<Composer disabled={false} onSend={noop} draft="hello" maxChars={100} />);
    expect(text(html)).toContain("5/100");
  });
});
