/**
 * Course titles for display (UI polish A1).
 *
 * Rutgers' Schedule of Classes gives short, upper-case titles ("DATA
 * STRUCTURES", "NUMER ANLYS & COMPUT"); ingestion prefers `expandedTitle` when
 * Rutgers supplies one, which it does for only ~61% of records. This module
 * only changes CASE, plus whole-word abbreviation expansion from the reviewed
 * table below. It never guesses: an abbreviation that is not in the table, or
 * whose entry is not marked reviewed, is shown as written (title-cased).
 *
 * Titles that already have mixed case ("Data Management for Data Science",
 * "iOS Development") are someone's deliberate casing and are left untouched.
 */

/** Words kept lower-case unless first or last. */
const MINOR = new Set([
  "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "nor", "of", "on",
  "or", "over", "per", "the", "to", "via", "vs", "vs.", "with",
]);

/** Kept upper-case wherever they appear. */
const UPPER = new Set([
  "ai", "ap", "aids", "cs", "dna", "esl", "gis", "hiv", "html", "it", "lgbt", "lgbtq", "nj",
  "rna", "roi", "rotc", "sql", "stem", "ui", "un", "us", "usa", "ux", "2d", "3d",
]);

const ROMAN = /^(?=[ivxlcdm]+$)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$/i;
/** English words that happen to be valid Roman numerals. */
const ROMAN_LOOKALIKES = new Set(["mix", "dim", "mid", "lid", "mild", "did", "di", "mi", "cd", "dc", "lc", "mc", "md", "mcc"]);

export interface AbbreviationEntry {
  /** The whole word as SOC writes it, case-insensitive ("anlys"). */
  short: string;
  /** The expansion, in display case ("Analysis"). */
  expanded: string;
  /** Applied only when true: an owner checked it against a Rutgers source. */
  reviewed: boolean;
  /** Where the expansion was confirmed (catalog page, SOC expandedTitle pair...). */
  source: string | null;
}

/**
 * Reviewed SOC abbreviation mapping. Every entry starts `reviewed: false`
 * (proposed) and has no effect until an owner confirms it against a Rutgers
 * source and records that source. Add entries; never flip `reviewed` without
 * a source.
 */
export const SOC_ABBREVIATIONS: readonly AbbreviationEntry[] = [
  { short: "intro", expanded: "Introduction", reviewed: false, source: null },
  { short: "anlys", expanded: "Analysis", reviewed: false, source: null },
  { short: "numer", expanded: "Numerical", reviewed: false, source: null },
  { short: "comput", expanded: "Computing", reviewed: false, source: null },
  { short: "mgmt", expanded: "Management", reviewed: false, source: null },
  { short: "prin", expanded: "Principles", reviewed: false, source: null },
  { short: "fund", expanded: "Fundamentals", reviewed: false, source: null },
];

function expansions(table: readonly AbbreviationEntry[]): Map<string, string> {
  return new Map(table.filter((e) => e.reviewed && e.source).map((e) => [e.short.toLowerCase(), e.expanded]));
}

function capitalize(word: string): string {
  // "women's" -> "Women's"; "(intro" -> "(Intro"
  const i = word.search(/[A-Za-z]/);
  if (i < 0) return word;
  return word.slice(0, i) + word.charAt(i).toUpperCase() + word.slice(i + 1);
}

function caseWord(raw: string, first: boolean, last: boolean, table: Map<string, string>): string {
  const core = raw.replace(/^[^A-Za-z0-9]+|[^A-Za-z0-9.]+$/g, "");
  const lower = core.toLowerCase();
  const swap = (value: string) => raw.replace(core, value);
  if (!core) return raw;
  const expanded = table.get(lower);
  if (expanded) return swap(expanded);
  if (UPPER.has(lower) || (ROMAN.test(core) && core.length <= 4 && !ROMAN_LOOKALIKES.has(lower))) {
    return swap(core.toUpperCase());
  }
  if (!first && !last && MINOR.has(lower)) return swap(lower);
  return raw
    .toLowerCase()
    .split("-")
    .map((part) => capitalize(part))
    .join("-");
}

/**
 * Display form of a course title. Only all-upper or all-lower titles are
 * re-cased; `table` is the abbreviation mapping (reviewed entries only apply).
 */
export function courseTitle(title: string | null | undefined, table: readonly AbbreviationEntry[] = SOC_ABBREVIATIONS): string {
  const text = (title ?? "").trim().replace(/\s+/g, " ");
  if (!text) return "";
  const letters = text.replace(/[^A-Za-z]/g, "");
  const mixed = letters !== letters.toUpperCase() && letters !== letters.toLowerCase();
  if (mixed) return text;
  const map = expansions(table);
  const words = text.split(" ");
  return words.map((w, i) => caseWord(w, i === 0, i === words.length - 1, map)).join(" ");
}
