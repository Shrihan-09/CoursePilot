/**
 * Course titles for display (UI polish A1; home follow-ups item 1).
 *
 * Rutgers' Schedule of Classes sends a short `title` ("INTR DISCRET STRCT I")
 * and, for about 60% of records, an `expandedTitle` ("INTRODUCTION TO
 * DISCRETE STRUCTURES I"). Ingestion stores the expandedTitle as the course
 * title whenever SOC has one, so most titles arriving here ARE the sourced
 * full title; this module only title-cases them.
 *
 * Courses with no expandedTitle arrive with the short SOC title. For those
 * only, a per-course fallback (SOC_FULL_TITLES) may supply the full title -
 * and an entry applies only when an owner has reviewed it against a Rutgers
 * source, and only while the course still shows exactly that SOC title (if
 * Rutgers later adds an expandedTitle, the entry stops applying by itself).
 * Nothing is expanded word by word, and nothing is guessed.
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

export interface FullTitleEntry {
  /** The course ("01:198:323"). */
  course: string;
  /** The short SOC title this entry replaces, exactly as SOC sends it (no expandedTitle). */
  soc_title: string;
  /** The full title, in display case. */
  full_title: string;
  /** Applied only when true: an owner confirmed it against a Rutgers source. */
  reviewed: boolean;
  /** Where it was confirmed (catalog page URL, department page...). */
  source: string | null;
}

/**
 * Proposed full titles for courses whose SOC record has NO expandedTitle
 * (scripts/list_unexpanded_titles.py lists them all). Every entry is a
 * proposal until reviewed: it has no effect until `reviewed: true` and a
 * `source` are set by an owner.
 */
export const SOC_FULL_TITLES: readonly FullTitleEntry[] = [
  { course: "01:198:111", soc_title: "INTRO COMPUTER SCI", full_title: "Introduction to Computer Science", reviewed: false, source: null },
  { course: "01:198:323", soc_title: "NUMER ANLYS & COMPUT", full_title: "Numerical Analysis and Computing", reviewed: false, source: null },
  { course: "01:640:250", soc_title: "INTRO LINEAR ALGEBRA", full_title: "Introduction to Linear Algebra", reviewed: false, source: null },
  { course: "01:640:300", soc_title: "INTRO MATH REASONING", full_title: "Introduction to Mathematical Reasoning", reviewed: false, source: null },
];

const norm = (t: string) => t.trim().replace(/\s+/g, " ").toUpperCase();

/** The reviewed full title for this course, if one applies to the title shown. */
function reviewedFullTitle(course: string | null | undefined, title: string, table: readonly FullTitleEntry[]): string | null {
  if (!course) return null;
  const hit = table.find((e) => e.course === course && e.reviewed && !!e.source && norm(e.soc_title) === norm(title));
  return hit ? hit.full_title : null;
}

function capitalize(word: string): string {
  // "women's" -> "Women's"; "(intro" -> "(Intro"
  const i = word.search(/[A-Za-z]/);
  if (i < 0) return word;
  return word.slice(0, i) + word.charAt(i).toUpperCase() + word.slice(i + 1);
}

function caseWord(raw: string, first: boolean, last: boolean): string {
  const core = raw.replace(/^[^A-Za-z0-9]+|[^A-Za-z0-9.]+$/g, "");
  const lower = core.toLowerCase();
  const swap = (value: string) => raw.replace(core, value);
  if (!core) return raw;
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
 * Display form of a course title: the sourced full title (title-cased), or -
 * for a course with no expandedTitle - its reviewed full title when one
 * exists. Only all-upper or all-lower titles are re-cased.
 */
export function courseTitle(title: string | null | undefined, course?: string | null, table: readonly FullTitleEntry[] = SOC_FULL_TITLES): string {
  const text = (title ?? "").trim().replace(/\s+/g, " ");
  if (!text) return "";
  const full = reviewedFullTitle(course, text, table);
  if (full) return full;
  const letters = text.replace(/[^A-Za-z]/g, "");
  const mixed = letters !== letters.toUpperCase() && letters !== letters.toLowerCase();
  if (mixed) return text;
  const words = text.split(" ");
  return words.map((w, i) => caseWord(w, i === 0, i === words.length - 1)).join(" ");
}
