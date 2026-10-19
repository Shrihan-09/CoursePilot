"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { APPEARANCE_KEY, type Appearance, isAppearance, resolveDark } from "@/lib/theme";

interface ThemeContextValue {
  appearance: Appearance;
  setAppearance: (value: Appearance) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readStored(): Appearance {
  try {
    const value = window.localStorage.getItem(APPEARANCE_KEY);
    return isAppearance(value) ? value : "system";
  } catch {
    return "system";
  }
}

/** Owns the appearance after hydration; THEME_SCRIPT owned it before. */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [appearance, setState] = useState<Appearance>("system");

  useEffect(() => setState(readStored()), []);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () =>
      document.documentElement.classList.toggle("dark", resolveDark(appearance, media.matches));
    apply();
    if (appearance !== "system") return;
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [appearance]);

  const setAppearance = useCallback((value: Appearance) => {
    setState(value);
    try {
      if (value === "system") window.localStorage.removeItem(APPEARANCE_KEY);
      else window.localStorage.setItem(APPEARANCE_KEY, value);
    } catch {
      // Storage unavailable: the choice still applies for this visit.
    }
  }, []);

  return (
    <ThemeContext.Provider value={{ appearance, setAppearance }}>{children}</ThemeContext.Provider>
  );
}

export function useAppearance(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useAppearance must be used inside ThemeProvider");
  return value;
}
