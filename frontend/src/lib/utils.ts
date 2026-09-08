import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Standard shadcn/ui class merge helper. Required by generated components. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
