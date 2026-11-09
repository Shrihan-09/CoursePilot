"use client";

import { PageHeader } from "@/components/ui/feedback";
import { AssistantWorkspace, useAssistantPanel } from "@/features/assistant/assistant-panel";

/**
 * Assistant (Prompt 11 Part B): questions about my plan and progress, and plan
 * changes that apply only when I confirm them. CoursePilot never signs in to
 * WebReg or registers anyone; it prepares the index numbers instead.
 */
export default function AssistantPage() {
  const panel = useAssistantPanel();
  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Planning"
        title="Assistant"
        description="Ask about your plan, your progress or a course. Changes are previews until you confirm them."
      />
      <div className="mx-auto max-w-3xl rounded-lg border border-border bg-surface-elevated">
        {panel?.isOpen ? <p className="p-4 text-sm text-fg-muted">Your conversation is open in the side panel.</p> : <AssistantWorkspace />}
      </div>
    </div>
  );
}
