"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * GPA visibility (UI polish B3). Hidden by default everywhere. The eye toggle
 * reveals it on THIS device only: one localStorage flag holding "1", never the
 * GPA itself, never synced to the server, never logged or sent to analytics.
 * Share views and Home never render it, whatever this flag says.
 */
export const GPA_VISIBLE_KEY = "cp-gpa-visible";

export function readGpaVisible(storage: Pick<Storage, "getItem"> | null = safeStorage()): boolean {
  try {
    return storage?.getItem(GPA_VISIBLE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeGpaVisible(visible: boolean, storage: Pick<Storage, "setItem" | "removeItem"> | null = safeStorage()): void {
  try {
    if (visible) storage?.setItem(GPA_VISIBLE_KEY, "1");
    else storage?.removeItem(GPA_VISIBLE_KEY);
  } catch {
    /* Storage disabled: the reveal lasts for this page only. */
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function useGpaVisible(): [boolean, (visible: boolean) => void] {
  // Hidden on the server render and the first client render; the stored choice applies after mount.
  const [visible, setVisible] = useState(false);
  useEffect(() => setVisible(readGpaVisible()), []);
  const set = useCallback((next: boolean) => {
    writeGpaVisible(next);
    setVisible(next);
  }, []);
  return [visible, set];
}
