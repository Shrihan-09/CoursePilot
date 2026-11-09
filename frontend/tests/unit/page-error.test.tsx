import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AppError from "@/app/error";
import SignedInError from "@/app/(app)/error";
import GlobalError from "@/app/global-error";

describe("page failure fallback", () => {
  const error = new Error("sensitive server diagnostics must stay out of the page");
  for (const [name, Component] of [["app", AppError], ["signed-in", SignedInError], ["root", GlobalError]] as const) {
    it(`${name} offers recovery without exposing the exception`, () => {
      const html = renderToStaticMarkup(<Component error={error} reset={() => {}} />);
      expect(html).toContain("This page couldn&#x27;t load");
      expect(html).toContain("Try again");
      expect(html).toContain('href="/home"');
      expect(html).toContain('role="alert"');
      expect(html).not.toContain(error.message);
      expect(html).not.toContain("Internal Server Error");
      if (name === "root") expect(html).toContain('<html lang="en">');
    });
  }
});
