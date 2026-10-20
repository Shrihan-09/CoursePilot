import {
  Bell,
  BookOpen,
  CalendarDays,
  GraduationCap,
  Home,
  Map,
  MessageSquareText,
  Settings,
  UserRound,
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
  /** Sidebar section (Sunsama-style small caps label); none = top of the list. */
  group?: "Academics" | "Planning" | "Coming later" | "Account";
}

export const NAV: NavItem[] = [
  { href: "/home", label: "Home", icon: Home, available: true, mobile: "tab" },
  { href: "/degree", label: "Degree", icon: GraduationCap, available: true, mobile: "tab", group: "Academics" },
  { href: "/profile", label: "Progress", icon: UserRound, available: true, mobile: "more", group: "Academics" },
  { href: "/plan", label: "Plan", icon: Map, available: true, mobile: "tab", group: "Planning" },
  { href: "/schedule", label: "Schedule", icon: CalendarDays, available: true, mobile: "tab", group: "Planning" },
  { href: "/courses", label: "Courses", icon: BookOpen, available: false, mobile: "more", group: "Coming later" },
  { href: "/notifications", label: "Notifications", icon: Bell, available: false, mobile: "more", group: "Coming later" },
  { href: "/assistant", label: "Assistant", icon: MessageSquareText, available: false, mobile: "more", group: "Coming later" },
  { href: "/settings", label: "Settings", icon: Settings, available: true, mobile: "more", group: "Account" },
];
