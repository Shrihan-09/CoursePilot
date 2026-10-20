import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProgressResponse, ProgressMetric } from "@/types/progress";
const query = vi.hoisted(() => ({ data: null as ProgressResponse | null }));
vi.mock("@tanstack/react-query", () => ({ useQuery: () => ({ data: query.data, isPending: false, isError: false }) }));
import { ProgressSummary } from "@/features/progress/progress-summary";

const part = (percent: string | null) => ({ units: { numerator: 1, denominator: 3 }, percent,
  exact_percent: percent === null ? null : { numerator: 100, denominator: 3 } });
const metric = (): ProgressMetric => ({ scope: "modeled_overall", label: "Modeled requirements",
  availability: "available", denominator_units: 3, denominator_definition: "Fabricated fixed obligations",
  completed: part("33.33"), projected_current: part("66.67"), projected_plan: part("100.00"),
  remaining: part("66.67"), unknown_obligations: 0, percentage_kind: "exact", status: "in_progress",
  drill_down: "/profile#modeled_overall", requirement_codes: [], requirement_results: [], allocations: [],
  explanation: "Projected work is conditional." });
function html() { return renderToStaticMarkup(<ProgressSummary />); }

describe("modeled progress proof", () => {
  beforeEach(() => { query.data = { schema_version: "1", metric_policy_version: "1",
    display_percent_decimal_places: 2, coverage: "partial", graduation_eligibility: "not_assessed",
    coverage_limitations: ["Fabricated scope only"], projection_state: "available", metrics: [metric()] }; });
  it("displays server values with distinct completed and conditional meanings", () => {
    const result = html();
    expect(result).toContain("33.33");
    expect(result).toContain("66.67");
    expect(result).toContain("100.00");
    expect(result).toContain("conditional");
    expect(result).toContain("do not establish eligibility to graduate");
    expect(result).toContain("Exact completed percentage");
    expect(result).toContain("100/3");
  });
  it("never manufactures a percentage for an unavailable scope", () => {
    query.data!.metrics[0] = { ...metric(), availability: "unavailable", denominator_units: 0,
      completed: part(null), projected_current: part(null), projected_plan: null };
    const result = html();
    expect(result).toContain("No percentage is assumed");
    expect(result).not.toContain("0% completed");
  });
  it("makes uncertainty and stale saved projections visible in words", () => {
    query.data!.metrics[0]!.unknown_obligations = 2;
    query.data!.metrics[0]!.projected_plan = null;
    query.data!.projection_state = "stale";
    const result = html();
    expect(result).toContain("confirmed lower bound");
    expect(result).toContain("out of date");
    expect(result).not.toContain("With the saved plan");
    expect(result).toContain("<summary");
  });
});
