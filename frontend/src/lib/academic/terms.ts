/**
 * Rutgers term codes (YYYYT; T = 0 winter, 1 spring, 7 summer, 9 fall) for
 * choosing a plan's start term. Calendar arithmetic only, mirroring
 * backend/app/services/planning/terms.py; whether a start term is ACCEPTED
 * is still the API's decision (it refuses one at or before the latest
 * recorded term).
 */

const ORDER = ["0", "1", "7", "9"] as const;

export function nextTerm(code: string): string {
  const year = Number(code.slice(0, 4));
  const i = ORDER.indexOf(code[4] as (typeof ORDER)[number]);
  return i + 1 < ORDER.length ? `${year}${ORDER[i + 1]}` : `${year + 1}${ORDER[0]}`;
}

/** `count` start-term choices from `first` (inclusive), Fall/Spring unless widened. */
export function startTermChoices(first: string, count: number, opts: { summer: boolean; winter: boolean }): string[] {
  const allowed = new Set(["1", "9", ...(opts.summer ? ["7"] : []), ...(opts.winter ? ["0"] : [])]);
  const out: string[] = [];
  let code = first;
  for (let guard = 0; out.length < count && guard < 64; guard += 1) {
    if (allowed.has(code[4] ?? "")) out.push(code);
    code = nextTerm(code);
  }
  return out;
}
