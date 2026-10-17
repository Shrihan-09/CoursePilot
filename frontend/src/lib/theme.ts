/**
 * Appearance preference (Phase 7.1).
 *
 * system | light | dark, initially "system". Stored under ONE localStorage key
 * holding only that word - never next to any student data. Applied by
 * THEME_SCRIPT, which runs in <head> before first paint, so the wrong theme
 * never flashes; the React provider takes over after hydration.
 */

export const APPEARANCES = ["system", "light", "dark"] as const;
export type Appearance = (typeof APPEARANCES)[number];
export const APPEARANCE_KEY = "cp-appearance";

export function isAppearance(value: unknown): value is Appearance {
  return typeof value === "string" && (APPEARANCES as readonly string[]).includes(value);
}

export function resolveDark(appearance: Appearance, systemDark: boolean): boolean {
  return appearance === "dark" || (appearance === "system" && systemDark);
}

/** Inlined verbatim into <head>. Must not throw: storage can be unavailable. */
export const THEME_SCRIPT = `(function(){try{var a=localStorage.getItem("${APPEARANCE_KEY}");
if(a!=="light"&&a!=="dark")a="system";var d=a==="dark"||(a==="system"&&
window.matchMedia("(prefers-color-scheme: dark)").matches);
document.documentElement.classList.toggle("dark",d);}catch(e){}})();`;
