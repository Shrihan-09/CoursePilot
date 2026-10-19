"use client";

import { useQuery } from "@tanstack/react-query";
import { Monitor, Moon, Sun } from "lucide-react";

import { useAppearance } from "@/components/theme/theme-provider";
import { Button } from "@/components/ui/button";
import { PageHeader, Skeleton } from "@/components/ui/feedback";
import { api } from "@/lib/api";
import type { Appearance } from "@/lib/theme";
import { cn } from "@/lib/utils";

const OPTIONS: { value: Appearance; label: string; icon: typeof Sun }[] = [
  { value: "system", label: "System", icon: Monitor },
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
];

const INSTITUTION: Record<string, string> = {
  verified: "Your identity provider confirms an accepted Rutgers account. This does not confirm current enrollment.",
  not_verified: "Your identity provider didn't confirm an accepted Rutgers account.",
  not_configured: "CoursePilot doesn't check institution membership on this server.",
};

export default function SettingsPage() {
  const { appearance, setAppearance } = useAppearance();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const profile = useQuery({ queryKey: ["profile"], queryFn: api.profile });

  return (
    <div className="space-y-8">
      <PageHeader eyebrow="Settings" title="Settings" />

      <section aria-labelledby="appearance" className="space-y-3">
        <h2 id="appearance" className="text-sm font-semibold text-fg">Appearance</h2>
        <div role="radiogroup" aria-labelledby="appearance"
             className="grid max-w-md grid-cols-3 gap-1 rounded-lg border border-border bg-surface p-1">
          {OPTIONS.map(({ value, label, icon: Icon }) => {
            const selected = appearance === value;
            return (
              <button
                key={value}
                role="radio"
                aria-checked={selected}
                onClick={() => setAppearance(value)}
                className={cn(
                  "flex h-10 items-center justify-center gap-2 rounded text-sm font-medium transition-colors",
                  selected ? "bg-surface-elevated text-fg shadow-elevated" : "text-fg-secondary hover:text-fg",
                )}
              >
                <Icon aria-hidden className={cn("size-4", selected && "text-primary")} />
                {label}
              </button>
            );
          })}
        </div>
        <p className="text-xs text-fg-muted">Saved on this device only.</p>
      </section>

      <section aria-labelledby="account" className="space-y-3">
        <h2 id="account" className="text-sm font-semibold text-fg">Account</h2>
        <div className="divide-y divide-border rounded-lg border border-border bg-surface-elevated">
          {me.isPending ? (
            <div className="p-4"><Skeleton className="h-10" /></div>
          ) : me.data ? (
            <>
              <Row label="Sign-in">
                {me.data.sign_in_method === "dev" ? "Development sign-in (not for real use)" : "Your identity provider"}
              </Row>
              <Row label="Institution">{INSTITUTION[me.data.institution.status]}</Row>
            </>
          ) : null}
          <Row label="Privacy">
            CoursePilot knows who you are from your sign-in and nothing else. It never sees your
            Rutgers password or accesses your Rutgers records, email or files.
          </Row>
        </div>
      </section>

      <section aria-labelledby="academic" className="space-y-3">
        <h2 id="academic" className="text-sm font-semibold text-fg">Academic profile</h2>
        <div className="divide-y divide-border rounded-lg border border-border bg-surface-elevated">
          {profile.isPending ? (
            <div className="p-4"><Skeleton className="h-10" /></div>
          ) : profile.data ? (
            <>
              <Row label="Program">
                <span className="[overflow-wrap:anywhere]">{profile.data.program.program_name}</span>
              </Row>
              <Row label="Catalog year"><span className="tabular">{profile.data.program.catalog_year}</span></Row>
            </>
          ) : null}
          <Row label="Source">Only what you uploaded or entered and confirmed.</Row>
        </div>
      </section>

      <form action="/auth/logout" method="post">
        <Button type="submit" variant="danger">Sign out</Button>
      </form>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 p-4 sm:grid-cols-[10rem_1fr] sm:gap-4">
      <p className="text-sm font-medium text-fg">{label}</p>
      <div className="text-sm text-fg-secondary">{children}</div>
    </div>
  );
}
