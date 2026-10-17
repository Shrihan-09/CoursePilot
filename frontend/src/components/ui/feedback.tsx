import { AlertTriangle, CheckCircle2, CircleAlert, Info, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";

type Tone = "neutral" | "info" | "success" | "warning" | "danger";

const toneClass: Record<Tone, string> = {
  neutral: "border-border bg-surface text-fg-secondary",
  info: "border-primary/25 bg-primary-subtle text-primary-text",
  success: "border-success/25 bg-success-subtle text-success",
  warning: "border-warning/30 bg-warning-subtle text-warning",
  danger: "border-danger/30 bg-danger-subtle text-danger",
};

const toneIcon: Record<Tone, typeof Info> = {
  neutral: Info,
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  danger: XCircle,
};

export function Badge({
  tone = "neutral",
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium",
        toneClass[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** A status that NEVER relies on colour alone: icon + text label, always. */
export function StatusBadge({
  tone,
  label,
  icon,
  className,
}: {
  tone: Tone;
  label: string;
  icon?: typeof Info;
  className?: string;
}) {
  const Icon = icon ?? toneIcon[tone];
  return (
    <Badge tone={tone} className={className}>
      <Icon aria-hidden className="size-3.5 shrink-0" />
      <span>{label}</span>
    </Badge>
  );
}

/** Serious, never alarming: a calm panel with an icon and a plain sentence. */
export function Alert({
  tone = "info",
  title,
  children,
  action,
  className,
}: {
  tone?: Tone;
  title?: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  const Icon = toneIcon[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={cn("flex gap-3 rounded border p-3 text-sm", toneClass[tone], className)}
    >
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1 space-y-1">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className="text-fg-secondary">{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export function Progress({
  value,
  max = 100,
  label,
  className,
}: {
  value: number | null;
  max?: number;
  label: string;
  className?: string;
}) {
  const indeterminate = value === null;
  const pct = indeterminate ? 0 : Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={indeterminate ? undefined : value}
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-surface-hover", className)}
    >
      <div
        className={cn(
          "h-full rounded-full bg-primary transition-[width] duration-300",
          indeterminate && "w-1/3 animate-[cp-indeterminate_1.2s_ease-in-out_infinite]",
        )}
        style={indeterminate ? undefined : { width: `${pct}%` }}
      />
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        "animate-cp-shimmer rounded bg-[length:200%_100%]",
        "bg-[linear-gradient(90deg,rgb(var(--surface-hover))_25%,rgb(var(--surface))_50%,rgb(var(--surface-hover))_75%)]",
        className,
      )}
    />
  );
}

export function EmptyState({
  icon: Icon = Info,
  title,
  children,
  action,
}: {
  icon?: typeof Info;
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border-strong px-6 py-10 text-center">
      <Icon aria-hidden className="size-6 text-fg-muted" />
      <div className="space-y-1">
        <p className="text-sm font-medium text-fg">{title}</p>
        {children && <div className="text-sm text-fg-muted">{children}</div>}
      </div>
      {action}
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  children,
  onRetry,
  requestId,
}: {
  title?: string;
  children?: React.ReactNode;
  onRetry?: () => void;
  requestId?: string | null;
}) {
  return (
    <div role="alert" className="flex flex-col items-center gap-3 rounded-lg border border-danger/30 bg-danger-subtle/40 px-6 py-8 text-center">
      <CircleAlert aria-hidden className="size-6 text-danger" />
      <div className="space-y-1">
        <p className="text-sm font-medium text-fg">{title}</p>
        {children && <div className="text-sm text-fg-secondary">{children}</div>}
        {requestId && (
          <p className="code text-xs text-fg-muted">Reference: {requestId}</p>
        )}
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="h-10 rounded border border-border-strong bg-surface-elevated px-4 text-sm font-medium hover:bg-surface-hover"
        >
          Try again
        </button>
      )}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  eyebrow?: string;
}) {
  return (
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0 space-y-1">
        {eyebrow && <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">{eyebrow}</p>}
        <h1 className="text-2xl font-semibold tracking-tight text-fg">{title}</h1>
        {description && <p className="max-w-2xl text-sm text-fg-secondary">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 gap-2">{actions}</div>}
    </header>
  );
}
