import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { DesignGallery } from "./gallery";

export const metadata: Metadata = { title: "Design system" };

/** Visual QA surface for the design system and motion language. Not in production. */
export default function DesignPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <DesignGallery />;
}
