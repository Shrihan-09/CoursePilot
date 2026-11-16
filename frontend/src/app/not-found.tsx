import { StatusPage } from "@/components/feedback/page-error";

export const metadata = { title: "Page not found" };

export default function NotFound() {
  return (
    <StatusPage
      code="404"
      title="Page not found"
      body="That page doesn't exist or has moved. Your plan and record are where you left them."
      primary={{ label: "Go home", href: "/home" }}
      secondary={{ label: "Open your plan", href: "/plan" }}
    />
  );
}
