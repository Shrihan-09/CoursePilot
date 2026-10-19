import {
  Bell,
  BookOpen,
  CalendarDays,
  GraduationCap,
  Home,
  Map,
  MessageSquareText,
  Settings,
  type LucideIcon,
} from "lucide-react";

/**
 * The product's areas (Phase 7.1 shell). `available: false` areas are part of
 * the architecture and shown as "coming later" - never as dead links.
 * `mobile`: "tab" = bottom bar; "more" = the More sheet.
 */
export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  available: boolean;
  mobile: "tab" | "more";
}

export const NAV: NavItem[] = [
  { href: "/home", label: "Home", icon: Home, available: true, mobile: "tab" },
  { href: "/degree", label: "Degree", icon: GraduationCap, available: false, mobile: "tab" },
  { href: "/plan", label: "Plan", icon: Map, available: false, mobile: "tab" },
  { href: "/schedule", label: "Schedule", icon: CalendarDays, available: false, mobile: "tab" },
  { href: "/courses", label: "Courses", icon: BookOpen, available: false, mobile: "more" },
  { href: "/notifications", label: "Notifications", icon: Bell, available: false, mobile: "more" },
  { href: "/assistant", label: "Assistant", icon: MessageSquareText, available: false, mobile: "more" },
  { href: "/settings", label: "Settings", icon: Settings, available: true, mobile: "more" },
];
