import type { Config } from "tailwindcss";

/** Semantic tokens only (src/app/globals.css). No literal colours in components. */
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: token("background"),
        surface: {
          DEFAULT: token("surface"),
          elevated: token("surface-elevated"),
          hover: token("surface-hover"),
        },
        primary: {
          DEFAULT: token("primary"),
          hover: token("primary-hover"),
          foreground: token("primary-foreground"),
          subtle: token("primary-subtle"),
          text: token("primary-text"),
        },
        border: { DEFAULT: token("border"), strong: token("border-strong") },
        focus: token("focus"),
        success: { DEFAULT: token("success"), subtle: token("success-subtle") },
        warning: { DEFAULT: token("warning"), subtle: token("warning-subtle") },
        danger: { DEFAULT: token("danger"), subtle: token("danger-subtle") },
        overlay: token("overlay"),
      },
      textColor: {
        // "fg" not "primary": blue text is text-primary-text, never ambiguous.
        fg: {
          DEFAULT: token("text-primary"),
          secondary: token("text-secondary"),
          muted: token("text-muted"),
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        // Compact but comfortable: dense academic data stays readable.
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        DEFAULT: "var(--radius)",
        md: "var(--radius)",
        lg: "var(--radius-lg)",
      },
      boxShadow: { elevated: "var(--shadow-elevated)" },
      keyframes: {
        "cp-shimmer": { "0%": { backgroundPosition: "-200% 0" },
                        "100%": { backgroundPosition: "200% 0" } },
        "cp-fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "cp-slide-up": { from: { opacity: "0", transform: "translateY(8px)" },
                         to: { opacity: "1", transform: "translateY(0)" } },
      },
      animation: {
        "cp-shimmer": "cp-shimmer 1.6s linear infinite",
        "cp-fade-in": "cp-fade-in 160ms ease-out",
        "cp-slide-up": "cp-slide-up 200ms cubic-bezier(0.2, 0.8, 0.2, 1)",
      },
    },
  },
  plugins: [],
};

export default config;
