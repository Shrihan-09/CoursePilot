"use client";

import { PageHeader } from "@/components/ui/feedback";
import { RecordStep } from "@/features/setup/steps";

/** 10a follow-up: the setup wizard's "Bring your record" cards, after setup -
 * where the "Your grades are in" reminder sends a student to upload. Every
 * card leads here (no class-standing order): the reminder asks for both. */
export default function RecordsPage() {
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Records" title="Your records" description="Upload an updated transcript, Degree Navigator audit or schedule. You review each before it's saved." />
      <RecordStep standing={null} />
    </div>
  );
}
