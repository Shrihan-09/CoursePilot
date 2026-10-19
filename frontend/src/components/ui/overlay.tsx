"use client";

import { X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";

import { cn } from "@/lib/utils";

import { IconButton } from "./button";

/**
 * Dialog and Sheet are the NATIVE <dialog> element in modal mode: the browser
 * provides the focus trap, Escape to close, inert background and the
 * accessible modal role - nothing to re-implement or get subtly wrong.
 */
function useNativeDialog(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const handle = () => onClose();
    el.addEventListener("close", handle);
    return () => el.removeEventListener("close", handle);
  }, [onClose]);
  return ref;
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children?: React.ReactNode;
  footer?: React.ReactNode;
}) {
  const ref = useNativeDialog(open, onClose);
  const id = useId();
  return (
    <dialog
      ref={ref}
      aria-labelledby={`${id}-title`}
      aria-describedby={description ? `${id}-desc` : undefined}
      className="w-[min(32rem,calc(100vw-2rem))] rounded-lg border border-border bg-surface-elevated p-0 text-fg shadow-elevated backdrop:bg-overlay/50"
    >
      <div className="space-y-4 p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <h2 id={`${id}-title`} className="text-base font-semibold">
              {title}
            </h2>
            {description && (
              <p id={`${id}-desc`} className="text-sm text-fg-secondary">
                {description}
              </p>
            )}
          </div>
          <IconButton label="Close" onClick={onClose} className="-mr-2 -mt-2">
            <X className="size-4" />
          </IconButton>
        </div>
        {children}
      </div>
      {footer && (
        <div className="flex flex-col-reverse gap-2 border-t border-border bg-surface px-5 py-3 sm:flex-row sm:justify-end">
          {footer}
        </div>
      )}
    </dialog>
  );
}

/** A drawer: from the right on wide screens, from the bottom on phones. */
export function Sheet({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children?: React.ReactNode;
}) {
  const ref = useNativeDialog(open, onClose);
  const id = useId();
  return (
    <dialog
      ref={ref}
      aria-labelledby={`${id}-title`}
      className={cn(
        "m-0 max-h-[85dvh] w-full max-w-none rounded-t-lg border border-border bg-surface-elevated p-0 text-fg shadow-elevated",
        "mt-auto backdrop:bg-overlay/50",
        "sm:ml-auto sm:mt-0 sm:h-dvh sm:max-h-none sm:w-[24rem] sm:rounded-none sm:rounded-l-lg",
      )}
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <h2 id={`${id}-title`} className="text-sm font-semibold">
          {title}
        </h2>
        <IconButton label="Close" onClick={onClose}>
          <X className="size-4" />
        </IconButton>
      </div>
      <div className="overflow-y-auto p-4">{children}</div>
    </dialog>
  );
}

/** Lightweight tooltip: shown on hover AND keyboard focus, wired with aria-describedby. */
export function Tooltip({ content, children }: { content: string; children: React.ReactElement }) {
  const id = useId();
  return (
    <span className="group relative inline-flex">
      <span aria-describedby={id} className="inline-flex">
        {children}
      </span>
      <span
        id={id}
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 hidden w-max max-w-64 -translate-x-1/2 rounded px-2 py-1 text-xs group-focus-within:block group-hover:block"
        style={{ backgroundColor: "rgb(var(--text-primary))", color: "rgb(var(--background))" }}
      >
        {content}
      </span>
    </span>
  );
}

/* ------------------------------------------------------------------ toasts */

type ToastTone = "neutral" | "success" | "danger";
interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

const ToastContext = createContext<((message: string, tone?: ToastTone) => void) | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((message: string, tone: ToastTone = "neutral") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, message, tone }]);
    window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 5000);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex flex-col items-center gap-2 px-4 sm:bottom-6"
      >
        {items.map((t) => (
          <div
            key={t.id}
            className={cn(
              "pointer-events-auto max-w-sm animate-cp-slide-up rounded border px-4 py-2.5 text-sm shadow-elevated",
              t.tone === "success" && "border-success/30 bg-surface-elevated text-fg",
              t.tone === "danger" && "border-danger/30 bg-surface-elevated text-fg",
              t.tone === "neutral" && "border-border bg-surface-elevated text-fg",
            )}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const push = useContext(ToastContext);
  if (!push) throw new Error("useToast must be used inside ToastProvider");
  return push;
}

/* -------------------------------------------------------------------- tabs */

export function Tabs<T extends string>({
  value,
  onChange,
  items,
  label,
}: {
  value: T;
  onChange: (value: T) => void;
  items: { value: T; label: string; count?: number }[];
  label: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: React.KeyboardEvent, index: number) => {
    const next =
      e.key === "ArrowRight" ? (index + 1) % items.length
      : e.key === "ArrowLeft" ? (index - 1 + items.length) % items.length
      : null;
    if (next === null) return;
    e.preventDefault();
    const target = items[next];
    if (!target) return;
    onChange(target.value);
    refs.current[next]?.focus();
  };
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-border">
      {items.map((item, i) => {
        const selected = item.value === value;
        return (
          <button
            key={item.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(item.value)}
            onKeyDown={(e) => onKey(e, i)}
            className={cn(
              "-mb-px flex h-10 shrink-0 items-center gap-1.5 border-b-2 px-3 text-sm font-medium transition-colors",
              selected
                ? "border-primary text-fg"
                : "border-transparent text-fg-muted hover:text-fg",
            )}
          >
            {item.label}
            {item.count !== undefined && (
              <span className="tabular rounded-sm bg-surface-hover px-1.5 text-xs text-fg-secondary">
                {item.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
