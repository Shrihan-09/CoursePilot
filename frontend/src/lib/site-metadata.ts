import type { Metadata } from "next";

import { APP_DESCRIPTION, APP_NAME } from "@/lib/brand";

/** Root metadata: titles, PWA name, Open Graph. Separate from layout.tsx so it
 * can be tested without loading fonts. */
export const siteMetadata: Metadata = {
  title: { default: APP_NAME, template: `%s · ${APP_NAME}` },
  description: APP_DESCRIPTION,
  applicationName: APP_NAME,
  // Absolute Open Graph URLs when deployed; Next falls back to localhost.
  metadataBase: process.env.NEXT_PUBLIC_SITE_URL ? new URL(process.env.NEXT_PUBLIC_SITE_URL) : undefined,
  appleWebApp: { title: APP_NAME },
  openGraph: {
    type: "website",
    siteName: APP_NAME,
    title: APP_NAME,
    description: APP_DESCRIPTION,
    images: [{ url: "/brand/coursepilot-mark-512.png", width: 512, height: 512, alt: APP_NAME }],
  },
  twitter: { card: "summary", title: APP_NAME, description: APP_DESCRIPTION },
};
