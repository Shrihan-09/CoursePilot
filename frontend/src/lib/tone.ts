/**
 * A stable course tone (globals.css .cp-tone-N): the same course gets the same
 * tone on every page and every render, so a course is recognisable across the
 * plan board and the week. Tones are presentation only - never a status.
 */
export function courseTone(course: string): string {
  let h = 0;
  for (let i = 0; i < course.length; i += 1) h = (h * 31 + course.charCodeAt(i)) >>> 0;
  return `cp-tone-${(h % 4) + 1}`;
}
