import Image from "next/image";

import { cn } from "@/lib/utils";

/**
 * The CoursePilot mark - the supplied logo with its baked black background
 * extracted to real transparency (scripts/brand-assets.mjs). Never recoloured,
 * never redrawn. `decorative` when a visible "CoursePilot" label sits beside it.
 */
export function Mark({
  size = 32,
  decorative = false,
  priority = false,
  className,
}: {
  size?: number;
  decorative?: boolean;
  priority?: boolean;
  className?: string;
}) {
  const src =
    size <= 64 ? "/brand/coursepilot-mark-128.png"
    : size <= 128 ? "/brand/coursepilot-mark-256.png"
    : "/brand/coursepilot-mark-512.png";
  return (
    <Image
      src={src}
      width={size}
      height={size}
      alt={decorative ? "" : "CoursePilot"}
      priority={priority}
      className={cn("select-none", className)}
      draggable={false}
    />
  );
}

export function Wordmark({ size = 28, className }: { size?: number; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <Mark size={size} decorative />
      <span className="text-[15px] font-semibold tracking-tight text-fg">CoursePilot</span>
    </span>
  );
}
