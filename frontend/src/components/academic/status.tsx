import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  CircleDot,
  Clock3,
  HelpCircle,
  Info,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import { StatusBadge } from "@/components/ui/feedback";
import type { IconKey, Presentation } from "@/lib/academic/vocabulary";

/**
 * The academic status icons. UNKNOWN (a question mark) and FAILED (a cross)
 * are different shapes, so the distinction survives without colour - and
 * without the label, for anyone scanning a long list.
 */
export const STATUS_ICON: Record<IconKey, LucideIcon> = {
  complete: CheckCircle2,
  progress: Clock3,
  partial: CircleDot,
  remaining: CircleDashed,
  unknown: HelpCircle,
  failed: XCircle,
  info: Info,
  attention: AlertTriangle,
};

export function StateBadge({ state, className }: { state: Presentation; className?: string }) {
  return (
    <StatusBadge tone={state.tone} icon={STATUS_ICON[state.icon]} label={state.label} className={className} />
  );
}
