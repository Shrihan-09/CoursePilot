import Link from "next/link";
import { PageHeader } from "@/components/ui/feedback";
import { PlacementEditor } from "@/features/onboarding/placement-step";
import { ProgressSummary } from "@/features/progress/progress-summary";

export default function ProfilePage() {
  return <div className="space-y-6">
    <PageHeader eyebrow="Profile" title="Your modeled progress" description="Completed, current and planned work stay distinct." />
    <ProgressSummary />
    <PlacementEditor />
    <div className="flex flex-wrap gap-4 text-sm text-primary-text">
      <Link className="inline-flex min-h-11 items-center underline" href="/plan/personalize">Planning preferences</Link>
      <Link className="inline-flex min-h-11 items-center underline" href="/schedule">Current schedule</Link>
      <Link className="inline-flex min-h-11 items-center underline" href="/settings">Academic history and account</Link>
    </div>
  </div>;
}
