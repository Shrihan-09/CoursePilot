/**
 * Prompt 10a follow-up. Each test pins a rule from the brief:
 *   - class standing orders "Bring your record" (first-year: WebReg, the rest
 *     under "Later"; transfer: DN first) and the AP line reveals DN;
 *   - a blocking retake defaults to ASAP; winter shows only when offered then;
 *     a summer choice that pushes back an unlocked course warns; the choice is
 *     a retake pin for that course and term; no dollar amounts;
 *   - the grades reminder words what is out of date and can be dismissed.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { GradesReminderView } from "@/features/setup/grades-reminder";
import { RetakeCard, RetakeList } from "@/features/setup/retakes";
import { RecordStep } from "@/features/setup/steps";
import {
  CLASS_STANDINGS,
  gradesReminderText,
  recordCardOrder,
  retakeChoice,
  withRetakePin,
} from "@/lib/setup";
import type { GradesReminder, Retake, RetakeOption } from "@/types/onboarding";
import type { CoursePin } from "@/types/personalization";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");
const wrap = (node: React.ReactNode) => renderToStaticMarkup(
  <QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>);
const cardOrder = (html: string) => [...html.matchAll(/data-card="(\w+)"/g)].map((m) => m[1]);

describe("class-standing paths", () => {
  it("orders the record cards by standing", () => {
    expect(recordCardOrder("first_year")).toEqual({ main: ["webreg"], later: ["transcript", "dn"] });
    expect(recordCardOrder("transfer")).toEqual({ main: ["dn", "transcript", "webreg"], later: [] });
    for (const s of ["sophomore", "junior", "senior", null] as const) {
      expect(recordCardOrder(s)).toEqual({ main: ["transcript", "dn", "webreg"], later: [] });
    }
    expect(CLASS_STANDINGS.map((c) => c.label)).toEqual(["First-year", "Sophomore", "Junior", "Senior", "Transfer"]);
  });

  it("a first-year sees WebReg first, the rest collapsed under Later, and the AP line", () => {
    const html = wrap(<RecordStep standing="first_year" />);
    expect(cardOrder(html)).toEqual(["webreg", "transcript", "dn"]);
    expect(html).toMatch(/<details[^>]*data-later/);
    expect(html).not.toMatch(/<details[^>]*\sopen/);
    const t = text(html);
    expect(t).toContain("Later — we'll remind you after your grades come in");
    expect(t).toContain("Have AP or transfer credit?");
    expect(t).toContain("Upload your Degree Navigator audit now");
    expect(t).toContain("Upload your current schedule");
  });

  it("the AP/transfer line reveals the DN card for first-years", () => {
    expect(recordCardOrder("first_year", true)).toEqual({ main: ["webreg", "dn"], later: ["transcript"] });
    // Other standings are unaffected by it.
    expect(recordCardOrder("junior", true)).toEqual(recordCardOrder("junior"));
  });

  it("a transfer student sees the DN audit first, explained", () => {
    const html = wrap(<RecordStep standing="transfer" />);
    expect(cardOrder(html)).toEqual(["dn", "transcript", "webreg"]);
    expect(html).not.toContain("data-later");
    expect(text(html)).toContain("Shows which transfer credits Rutgers accepted");
  });

  it("sophomores to seniors keep the current order, nothing collapsed", () => {
    const html = wrap(<RecordStep standing="senior" />);
    expect(cardOrder(html)).toEqual(["transcript", "dn", "webreg"]);
    expect(html).not.toContain("data-later");
    expect(text(html)).not.toContain("Have AP or transfer credit?");
  });
});

describe("retake timing", () => {
  const option = (key: RetakeOption["key"], term: string | null, evidence: RetakeOption["evidence"] = "historical",
    impacts: RetakeOption["impacts"] = []): RetakeOption =>
    ({ key, term_code: term, label: term ?? "Not available", evidence, evidence_label: `ev-${key}`, recommended: key === "asap", impacts });
  const retake = (over: Partial<Retake> = {}): Retake => ({
    course: "01:640:152", title: "Calc II", reason: "grade F earns no credit",
    unlocks: ["01:640:251", "01:198:206", "01:198:314", "01:198:336"].map((course) => ({ course, title: null, planned_term: "20279" })),
    blocking: true, default: "asap", chosen_term: null,
    options: [option("asap", "20271", "confirmed"), option("next_semester", "20271", "confirmed")], ...over,
  });
  const html = (r: Retake, pins: CoursePin[] = []) => renderToStaticMarkup(<RetakeCard retake={r} pins={pins} onChange={() => {}} />);

  it("a blocking retake defaults to ASAP (Recommended), and names what it unlocks", () => {
    const r = retake();
    expect(retakeChoice(r, [])).toBe("asap");
    const t = text(html(r));
    expect(t).toContain("Calc II ( 01:640:152 ): retake needed.");
    expect(t).toContain("Why: grade F earns no credit.");
    expect(t).toContain("Unlocks: 01:640:251, 01:198:206, 01:198:314…");
    expect(t).toContain("ASAP (Recommended)");
    expect(t).toContain("When do you want to retake it?");
    // A non-blocking one defaults to the next semester.
    expect(retakeChoice({ ...r, default: "next_semester" }, [])).toBe("next_semester");
  });

  it("winter appears only when the server lists it (offered that winter), with its evidence label", () => {
    expect(text(html(retake()))).not.toContain("Winter session");
    const withWinter = retake({ options: [option("asap", "20279"), option("winter", "20270"), option("next_semester", "20271", "none")] });
    const markup = html(withWinter);
    expect(text(markup)).toContain("Winter session");
    expect(text(markup)).toContain("ev-winter");
    // An option with no offering on record is shown, but can't be chosen.
    expect(markup).toMatch(/disabled=""[^>]*>[\s\S]*Next semester/);
  });

  it("a summer choice shows the delay warning when it pushes back an unlocked course", () => {
    const delay = { course: "01:640:251", term_code: "20279", planned_term: "20277", delayed: true,
      message: "Summer delays 01:640:251 to Fall 2027" };
    const onTrack = { ...delay, delayed: false, message: "This keeps 01:640:251 on track for Summer 2027" };
    const r = retake({ options: [option("asap", "20271", "confirmed", [onTrack]), option("summer", "20277", "historical", [delay])] });
    // Warned before choosing it (ASAP chosen: its on-track line shows too) ...
    const before = text(html(r));
    expect(before).toContain("Summer delays 01:640:251 to Fall 2027.");
    expect(before).toContain("This keeps 01:640:251 on track for Summer 2027.");
    // ... and once chosen.
    expect(text(html(r, withRetakePin([], "01:640:152", "20277")))).toContain("Summer delays 01:640:251 to Fall 2027.");
  });

  it("Next semester that is the same term as ASAP is shown, but ASAP carries the choice", () => {
    const markup = html(retake());               // both Spring 2027
    expect(text(markup)).toContain("Same semester as ASAP.");
    expect(markup).toMatch(/disabled=""[^>]*>[\s\S]*Next semester/);
  });

  it("the choice is saved as a retake pin for that course and term only", () => {
    const pins = withRetakePin([{ course: "01:198:111", term_code: "20279" }, { course: "01:640:152", term_code: "20271", retake: true }],
      "01:640:152", "20270");
    expect(pins).toEqual([{ course: "01:198:111", term_code: "20279" }, { course: "01:640:152", term_code: "20270", retake: true }]);
    const r = retake({ options: [option("asap", "20279"), option("winter", "20270"), option("next_semester", "20271")] });
    expect(retakeChoice(r, pins)).toBe("winter");
  });

  it("lists nothing when there is no retake, and the cost note never quotes a dollar amount", () => {
    const note = "Winter and summer courses are billed separately — check Rutgers' tuition page.";
    expect(text(renderToStaticMarkup(<RetakeList data={{ start_term: "20271", unavailable: null, retakes: [], cost_note: note }}
      pins={[]} onChange={() => {}} />))).toContain("No retakes needed");
    const t = text(renderToStaticMarkup(<RetakeList pins={[]} onChange={() => {}} data={{ start_term: "20271", unavailable: null,
      retakes: [retake({ options: [option("asap", "20279"), option("winter", "20270")] })], cost_note: note }} />));
    expect(t).toContain("billed separately");
    expect(t).not.toMatch(/\$\s?\d/);
  });
});

describe("grades reminder", () => {
  const r = (over: Partial<GradesReminder> = {}): GradesReminder => ({
    show: true, term_code: "20269", grades_date: "2027-01-04", source_url: "https://nbregistrar.rutgers.edu/",
    kinds: ["transcript", "degree_navigator"], ...over,
  });

  it("asks for what is out of date, and can be dismissed", () => {
    const t = text(renderToStaticMarkup(<GradesReminderView reminder={r()} onDismiss={() => {}} />));
    expect(t).toContain("Fall 2026 final grades are in");
    expect(t).toContain("Your grades are in — upload your updated transcript and Degree Navigator audit so your plan stays accurate.");
    expect(t).toContain("Dismiss");
    expect(gradesReminderText(["degree_navigator"])).toBe(
      "Your grades are in — upload your updated Degree Navigator audit so your plan stays accurate.");
  });

  it("shows nothing when the server says not to (before the grades date, imports current, or dismissed)", () => {
    expect(renderToStaticMarkup(<GradesReminderView reminder={r({ show: false })} onDismiss={() => {}} />)).toBe("");
  });
});
