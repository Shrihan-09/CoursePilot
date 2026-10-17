import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "CoursePilot",
    short_name: "CoursePilot",
    description: "Plan your Rutgers degree with deterministic, explainable answers.",
    start_url: "/",
    display: "standalone",
    background_color: "#0A0B0D",
    theme_color: "#0A0B0D",
    icons: [
      { src: "/brand/pwa-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/brand/pwa-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/brand/pwa-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
