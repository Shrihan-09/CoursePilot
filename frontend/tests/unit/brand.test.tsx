/** The rebrand: one product-name constant, the affiliation footer, metadata. */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import manifest from "@/app/manifest";
import { SiteFooter } from "@/components/shell/site-footer";
import { AFFILIATION_DISCLAIMER, APP_NAME } from "@/lib/brand";

describe("brand", () => {
  it("has one display name", () => {
    expect(APP_NAME).toBe("Coursenav");
  });

  it("puts the affiliation notice in the footer", () => {
    const html = renderToStaticMarkup(<SiteFooter />);
    expect(html).toContain("<footer");
    expect(html).toContain(
      "Coursenav is a student project and is not affiliated with or endorsed by Rutgers University.");
    expect(AFFILIATION_DISCLAIMER).toContain(APP_NAME);
  });

  it("names the installed app Coursenav", () => {
    const m = manifest();
    expect(m.name).toBe("Coursenav");
    expect(m.short_name).toBe("Coursenav");
  });

  it("uses the name in page titles and Open Graph", async () => {
    const { siteMetadata: metadata } = await import("@/lib/site-metadata");
    expect(metadata.title).toEqual({ default: "Coursenav", template: "%s · Coursenav" });
    expect(metadata.applicationName).toBe("Coursenav");
    expect(metadata.openGraph).toMatchObject({ siteName: "Coursenav", title: "Coursenav" });
  });
});
