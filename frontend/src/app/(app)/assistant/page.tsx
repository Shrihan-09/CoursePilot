"use client";

import { PageHeader } from "@/components/ui/feedback";
import { AssistantChat } from "@/features/assistant/assistant-chat";

/**
 * Assistant (Prompt 11 Part B): questions about my plan and progress, and plan
 * changes that apply only when I confirm them. CoursePilot never signs in to
 * WebReg or registers anyone; it prepares the index numbers instead.
 */
export default function AssistantPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Planning"
        title="Assistant"
        description="Ask about your plan, your progress or a course. Changes are previews until you confirm them."
      />
      <div className="mx-auto max-w-3xl rounded-lg border border-border bg-surface-elevated">
        <AssistantChat />
      </div>
    </div>
  );
}
