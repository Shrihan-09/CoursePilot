import { Search } from "lucide-react";
import { forwardRef, useId } from "react";

import { cn } from "@/lib/utils";

const control =
  "w-full rounded border border-border-strong bg-surface-elevated px-3 text-sm text-fg " +
  "placeholder:text-fg-muted transition-colors hover:border-fg-muted/60 " +
  "focus-visible:border-primary focus-visible:outline focus-visible:outline-2 " +
  "focus-visible:outline-offset-0 focus-visible:outline-focus/40 " +
  "disabled:cursor-not-allowed disabled:opacity-60 aria-[invalid=true]:border-danger";

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn(control, "h-10", className)} {...props} />
  ),
);
Input.displayName = "Input";

export const Select = forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select ref={ref} className={cn(control, "h-10 pr-8", className)} {...props}>
      {children}
    </select>
  ),
);
Select.displayName = "Select";

export const SearchInput = forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => (
  <div className={cn("relative", className)}>
    <Search
      aria-hidden
      className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-muted"
    />
    <input ref={ref} type="search" className={cn(control, "h-10 pl-9")} {...props} />
  </div>
));
SearchInput.displayName = "SearchInput";

/** Label + control + hint/error, wired with ids so screen readers announce them. */
export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: (ids: { id: string; describedBy?: string; invalid: boolean }) => React.ReactNode;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-fg">
        {label}
      </label>
      {children({ id, describedBy, invalid: Boolean(error) })}
      {hint && !error && (
        <p id={hintId} className="text-xs text-fg-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-xs font-medium text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

export function Checkbox({
  label,
  description,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string; description?: string }) {
  return (
    <label className="flex min-h-10 cursor-pointer items-start gap-3 py-1.5">
      <input type="checkbox" className="mt-0.5 size-4 accent-[rgb(var(--primary))]" {...props} />
      <span className="text-sm">
        <span className="text-fg">{label}</span>
        {description && <span className="block text-fg-muted">{description}</span>}
      </span>
    </label>
  );
}

export function Radio({
  label,
  description,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string; description?: string }) {
  return (
    <label className="flex min-h-10 cursor-pointer items-start gap-3 py-1.5">
      <input type="radio" className="mt-0.5 size-4 accent-[rgb(var(--primary))]" {...props} />
      <span className="text-sm">
        <span className="text-fg">{label}</span>
        {description && <span className="block text-fg-muted">{description}</span>}
      </span>
    </label>
  );
}
