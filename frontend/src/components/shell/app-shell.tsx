"use client";

import { LogOut, MoreHorizontal } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { Wordmark } from "@/components/brand/mark";
import { Sheet, ToastProvider } from "@/components/ui/overlay";
import { NAV, type NavItem } from "@/lib/navigation";
import { cn } from "@/lib/utils";

function isActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function SidebarLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  const body = (
    <>
      <Icon aria-hidden className={cn("size-[18px]", active ? "text-primary" : "text-fg-muted")} />
      <span className="flex-1">{item.label}</span>
      {!item.available && <span className="text-2xs font-medium text-fg-muted">Later</span>}
    </>
  );
  const base = "flex h-9 items-center gap-3 rounded px-3 text-sm font-medium";
  if (!item.available) {
    return (
      <span aria-disabled className={cn(base, "cursor-default text-fg-muted/80")}>
        {body}
      </span>
    );
  }
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        base,
        "transition-colors",
        active ? "bg-primary-subtle text-fg" : "text-fg-secondary hover:bg-surface-hover hover:text-fg",
      )}
    >
      {body}
    </Link>
  );
}

function SignOut({ compact }: { compact?: boolean }) {
  return (
    <form action="/auth/logout" method="post">
      <button
        type="submit"
        className={cn(
          "flex h-9 w-full items-center gap-3 rounded px-3 text-sm font-medium text-fg-secondary hover:bg-surface-hover hover:text-fg",
          compact && "h-11",
        )}
      >
        <LogOut aria-hidden className="size-[18px] text-fg-muted" />
        Sign out
      </button>
    </form>
  );
}

/**
 * Desktop/laptop (lg+): persistent sidebar. Phones and small tablets: top bar
 * + bottom tab bar (thumb reach) with secondary areas in a "More" sheet.
 */
export function AppShell({
  children,
  identity,
}: {
  children: React.ReactNode;
  identity: string | null;
}) {
  const pathname = usePathname();
  const [moreOpen, setMoreOpen] = useState(false);
  const tabs = NAV.filter((n) => n.mobile === "tab");
  const more = NAV.filter((n) => n.mobile === "more");

  return (
    <ToastProvider>
      <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
        <a
          href="#main"
          className="sr-only z-50 rounded bg-surface-elevated px-3 py-2 focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
        >
          Skip to content
        </a>

        {/* Desktop sidebar */}
        <aside className="sticky top-0 hidden h-dvh flex-col border-r border-border bg-surface lg:flex">
          <div className="flex h-14 items-center px-4">
            <Link href="/home" className="rounded" aria-label="CoursePilot home">
              <Wordmark />
            </Link>
          </div>
          <nav aria-label="Primary" className="flex-1 space-y-0.5 px-2 py-2">
            {NAV.map((item) => (
              <SidebarLink key={item.href} item={item} active={isActive(pathname, item.href)} />
            ))}
          </nav>
          <div className="space-y-1 border-t border-border p-2">
            {identity && (
              <p className="truncate px-3 py-1 text-xs text-fg-muted" title={identity}>
                {identity}
              </p>
            )}
            <SignOut />
          </div>
        </aside>

        <div className="flex min-h-dvh min-w-0 flex-col">
          {/* Mobile top bar */}
          <header className="sticky top-0 z-30 flex h-14 items-center border-b border-border bg-background/90 px-4 backdrop-blur lg:hidden">
            <Link href="/home" className="rounded" aria-label="CoursePilot home">
              <Wordmark size={26} />
            </Link>
          </header>

          <main id="main" className="mx-auto w-full max-w-5xl flex-1 px-4 pb-28 pt-6 sm:px-6 lg:px-10 lg:pb-12 lg:pt-10">
            {children}
          </main>
        </div>

        {/* Mobile bottom tab bar */}
        <nav
          aria-label="Primary"
          className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-border bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden"
        >
          {tabs.map((item) => {
            const Icon = item.icon;
            const active = isActive(pathname, item.href);
            const inner = (
              <>
                <Icon aria-hidden className={cn("size-5", active ? "text-primary" : "text-fg-muted")} />
                <span className={cn("text-2xs font-medium", active ? "text-fg" : "text-fg-muted")}>
                  {item.label}
                </span>
              </>
            );
            return item.available ? (
              <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined}
                    className="flex h-14 flex-col items-center justify-center gap-0.5">
                {inner}
              </Link>
            ) : (
              <span key={item.href} aria-disabled title={`${item.label} - coming later`}
                    className="flex h-14 flex-col items-center justify-center gap-0.5 opacity-55">
                {inner}
              </span>
            );
          })}
          <button
            type="button"
            onClick={() => setMoreOpen(true)}
            className="flex h-14 flex-col items-center justify-center gap-0.5"
            aria-haspopup="dialog"
          >
            <MoreHorizontal aria-hidden className="size-5 text-fg-muted" />
            <span className="text-2xs font-medium text-fg-muted">More</span>
          </button>
        </nav>

        <Sheet open={moreOpen} onClose={() => setMoreOpen(false)} title="More">
          <nav aria-label="More" className="space-y-0.5" onClick={() => setMoreOpen(false)}>
            {more.map((item) => (
              <SidebarLink key={item.href} item={item} active={isActive(pathname, item.href)} />
            ))}
            {identity && <p className="truncate px-3 pt-3 text-xs text-fg-muted">{identity}</p>}
            <SignOut compact />
          </nav>
        </Sheet>
      </div>
    </ToastProvider>
  );
}
