"use client";

import { useMutation } from "@tanstack/react-query";
import { Flag } from "lucide-react";
import { createContext, useContext, useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError, api } from "@/lib/api";

/**
 * Prompt 08 B1: "Report a wrong requirement" on every requirement row.
 *
 * Sends the program, the requirement node and the student's message - no
 * transcript, plan or grade. The program and catalog year come from the page
 * that renders the tree (a context); a tree without one shows no button.
 */
export interface ReportTarget {
  program_key: string;
  catalog_year: string;
}

export const ReportTargetContext = createContext<ReportTarget | null>(null);

export const MAX_MESSAGE = 2000;

export function ReportRequirement({ code, name }: { code: string; name: string }) {
  const target = useContext(ReportTargetContext);
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const fieldId = useId();
  const send = useMutation({
    mutationFn: () => api.reportRequirement({
      program_key: target!.program_key, catalog_year: target!.catalog_year,
      requirement_code: code, message: message.trim(),
    }),
  });
  if (!target) return null;

  if (send.isSuccess) {
    return (
      <p role="status" className="text-xs text-fg-muted">
        Thanks. Your report on &ldquo;{name}&rdquo; was sent; we&apos;ll check it against the Rutgers sources.
      </p>
    );
  }
  if (!open) {
    return (
      <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(true)}
              className="text-fg-muted" aria-label={`Report a problem with ${name}`}>
        <Flag aria-hidden className="size-3.5" /> Report a problem with this requirement
      </Button>
    );
  }
  const error = send.error instanceof ApiError ? send.error.message
    : send.isError ? "The report couldn't be sent. Try again." : null;
  const trimmed = message.trim();
  return (
    <form
      className="space-y-2 rounded border border-border p-3"
      onSubmit={(e) => { e.preventDefault(); if (trimmed) send.mutate(); }}
    >
      <label htmlFor={fieldId} className="block text-xs font-medium text-fg">
        What looks wrong with &ldquo;{name}&rdquo;?
      </label>
      <textarea
        id={fieldId}
        value={message}
        maxLength={MAX_MESSAGE}
        rows={3}
        onChange={(e) => setMessage(e.target.value)}
        className="w-full rounded border border-border-strong bg-surface p-2 text-sm text-fg"
        placeholder="For example: the catalog lists a different course, or a different number of courses."
      />
      <p className="text-xs text-fg-muted">
        Only this requirement and your message are sent - not your courses or grades. Please don&apos;t include personal details.
      </p>
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={!trimmed || send.isPending}>
          {send.isPending ? "Sending…" : "Send report"}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => { setOpen(false); send.reset(); }}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
