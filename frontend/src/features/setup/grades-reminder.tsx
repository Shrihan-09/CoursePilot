"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { Button, buttonVariants } from "@/components/ui/button";
import { Alert } from "@/components/ui/feedback";
import { termLabel } from "@/lib/academic/vocabulary";
import { api } from "@/lib/api";
import { gradesReminderText } from "@/lib/setup";
import type { GradesReminder } from "@/types/onboarding";

/** 10a follow-up: the dismissible Home alert after a term's final-grades date. */
export function GradesReminderAlert() {
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["grades-reminder"], queryFn: api.gradesReminder });
  const dismiss = useMutation({
    mutationFn: api.dismissGradesReminder,
    onSuccess: (r) => client.setQueryData(["grades-reminder"], r),
  });
  if (!q.data) return null;
  return <GradesReminderView reminder={q.data} onDismiss={() => dismiss.mutate()} busy={dismiss.isPending} />;
}

export function GradesReminderView({ reminder, onDismiss, busy = false }: {
  reminder: GradesReminder; onDismiss: () => void; busy?: boolean;
}) {
  if (!reminder.show) return null;
  return (
    <Alert tone="info" title={reminder.term_code ? `${termLabel(reminder.term_code)} final grades are in` : undefined}
      action={
        <div className="flex flex-wrap gap-2">
          <Link href="/records" className={buttonVariants({ variant: "secondary", size: "sm" })}>Upload</Link>
          <Button variant="ghost" size="sm" onClick={onDismiss} loading={busy}>Dismiss</Button>
        </div>
      }>
      {gradesReminderText(reminder.kinds)}
    </Alert>
  );
}
