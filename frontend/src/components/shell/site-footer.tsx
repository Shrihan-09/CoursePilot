import { AFFILIATION_DISCLAIMER } from "@/lib/brand";

/**
 * The affiliation notice, on every page (rendered by the root layout). Inside
 * the app shell it clears the fixed mobile tab bar - see `.site-footer` in
 * globals.css.
 */
export function SiteFooter() {
  return (
    <footer className="site-footer border-t border-border px-4 py-4 text-center text-xs leading-snug text-fg-muted">
      <p>{AFFILIATION_DISCLAIMER}</p>
    </footer>
  );
}
