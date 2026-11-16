import type { MetadataRoute } from "next";

/** UI polish B4: installable app. No push notifications. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "CoursePilot",
    short_name: "CoursePilot",
    description: "Plan your Rutgers degree with deterministic, explainable answers.",
    start_url: "/home",
    scope: "/",
    display: "standalone",
    background_color: "#0A0B0D",
    theme_color: "#0A0B0D",
    categories: ["education", "productivity"],
    icons: [
      { src: "/brand/pwa-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/brand/pwa-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/brand/pwa-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
