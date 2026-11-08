"use client";

import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Prompt 10a choice controls. Each is a NATIVE radio or checkbox (visually
 * hidden) inside its label, so arrow keys, Space, screen-reader roles and
 * form semantics come from the browser - the styling is the only custom part.
 */

/** A big tappable card for one choice in a group (single select). */
export function ChoiceCard({ name, checked, onSelect, title, detail, body, disabled = false, children }: {
  name: string;
  checked: boolean;
  onSelect: () => void;
  title: string;
  /** One line: e.g. its credit and major-course ranges. */
  detail?: string;
  body?: string;
  disabled?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <label
      className={cn(
        "relative flex min-h-24 flex-col gap-1 rounded-lg border bg-surface-elevated p-4 transition-[border-color,box-shadow] duration-200",
        "has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-focus",
        disabled ? "cursor-not-allowed border-dashed border-border bg-surface" :
          checked ? "cursor-pointer cp-glow-selected" : "cursor-pointer border-border hover:border-border-strong",
      )}
    >
      <input type="radio" name={name} checked={checked} onChange={onSelect} disabled={disabled} className="sr-only" />
      <span className="flex items-start justify-between gap-3">
        <span className="text-base font-semibold text-fg">{title}</span>
        <span
          aria-hidden
          className={cn("flex size-5 shrink-0 items-center justify-center rounded-full border",
            checked ? "border-primary bg-primary text-primary-foreground" : "border-border-strong")}
        >
          {checked && <Check className="size-3.5" />}
        </span>
      </span>
      {detail && <span className="tabular text-sm font-medium text-primary-text">{detail}</span>}
      {body && <span className="text-sm text-fg-secondary">{body}</span>}
      {children}
    </label>
  );
}

/** A pill: radio (one of a group) or checkbox (any number). */
export function Chip({ type = "radio", name, checked, onChange, label }: {
  type?: "radio" | "checkbox";
  name: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
}) {
  return (
    <label
      className={cn(
        "inline-flex min-h-11 cursor-pointer select-none items-center gap-1.5 rounded-full border px-4 text-sm font-medium transition-[border-color,box-shadow,background-color] duration-200",
        "has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-focus",
        checked ? "cp-glow-selected bg-primary-subtle text-fg" : "border-border-strong bg-surface-elevated text-fg-secondary hover:text-fg",
      )}
    >
      <input type={type} name={name} checked={checked} onChange={(e) => onChange(e.target.checked)} className="sr-only" />
      {checked && <Check aria-hidden className="size-3.5 text-primary-text" />}
      {label}
    </label>
  );
}

/** A labelled group of chips: a fieldset whose legend names the question. */
export function ChipGroup({ legend, hint, children }: { legend: string; hint?: string; children: React.ReactNode }) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-semibold text-fg">{legend}</legend>
      {hint && <p className="text-xs text-fg-muted">{hint}</p>}
      <div className="flex flex-wrap gap-2">{children}</div>
    </fieldset>
  );
}

/** The faint glowing line between sections. */
export function GlowRule() {
  return <hr aria-hidden className="cp-glow-rule" />;
}
