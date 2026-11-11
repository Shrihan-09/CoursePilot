/**
 * The assistant's stream (Prompt 11 Part B, B3).
 *
 * POST /student/assistant/messages answers with NDJSON: "activity" lines while
 * tools run ("Checked your plan"), then one "reply" (or "error"). The reply
 * text arrives only after the server's grounding gate passed it; the panel
 * reveals it progressively, but never shows unvalidated text.
 */

import type { Chip, MessageBody, StreamEvent } from "@/types/assistant";

/**
 * Longest message a student may type. The server sends its own value
 * (`max_input_chars` on the status); this is the fallback, and it matches the
 * backend default. The API refuses longer messages either way.
 */
export const ASSISTANT_MAX_INPUT_CHARS = 250;

/** Split NDJSON chunks into events; returns the events and the unfinished tail. */
export function parseNdjson(buffer: string): { events: StreamEvent[]; rest: string } {
  const lines = buffer.split("\n");
  const rest = lines.pop() ?? "";
  const events: StreamEvent[] = [];
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      events.push(JSON.parse(line) as StreamEvent);
    } catch {
      // A malformed line is skipped; the final reply/error still arrives.
    }
  }
  return { events, rest };
}

export async function streamMessage(
  body: MessageBody,
  onEvent: (event: StreamEvent) => void,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const response = await fetcher("/api/cp/student/assistant/messages", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/x-ndjson" },
    credentials: "same-origin",
    cache: "no-store",
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    let detail = `Request failed (${response.status})`;
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? detail;
    } catch {
      // not JSON
    }
    onEvent({ type: "error", status: response.status, detail });
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: !done });
    const { events, rest } = parseNdjson(done ? `${buffer}\n` : buffer);
    buffer = rest;
    events.forEach(onEvent);
    if (done) return;
  }
}

/** What a tapped chip sends: a clarify answer, a command, or plain text. */
export function chipBody(chip: Chip): MessageBody {
  return { chip: { label: chip.label, value: chip.value, question: chip.question ?? null } };
}

/** "async" / "online" / "hybrid" / "in person" as shown on a card. */
export function deliveryWords(delivery: string[]): string {
  return delivery.length ? delivery.join(" · ") : "format not published";
}

export const OPEN_STATUS: Record<string, string> = {
  open: "Open",
  closed: "Closed",
  unknown: "Seats unknown",
  not_published: "Sections not published yet",
};
