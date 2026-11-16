import { PageHeader } from "@/components/ui/feedback";
import { displayIdentity } from "@/lib/auth/session";
import { currentSession } from "@/lib/server-api";
import { DeclarationEditor } from "@/features/onboarding/declaration-editor";
import { PlacementEditor } from "@/features/onboarding/placement-step";
import { GradesPersonalization } from "@/features/planning/grades-personalization";
import { InstallApp, ProfileOverview } from "@/features/profile/profile-overview";
import { ProgressSummary } from "@/features/progress/progress-summary";

export default async function ProfilePage() {
  const session = await currentSession();
  const identity = session ? displayIdentity(session).email : null;
  return <div className="space-y-6">
    <PageHeader eyebrow="Profile" title="Your profile" description="Your program, uploads and preferences. Your GPA stays hidden unless you reveal it on this device." />
    <ProfileOverview identity={identity} />
    <GradesPersonalization />
    <InstallApp />
    <section aria-labelledby="modeled-heading" className="space-y-4 border-t border-border pt-6">
      <div className="space-y-1">
        <h2 id="modeled-heading" className="text-lg font-semibold text-fg">Modeled progress</h2>
        <p className="text-sm text-fg-secondary">Completed, current and planned work stay distinct.</p>
      </div>
      <ProgressSummary />
    </section>
    <DeclarationEditor />
    <PlacementEditor />
  </div>;
}
