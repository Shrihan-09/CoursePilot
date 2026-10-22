"""Phase 7.3 Step 2: MANUAL Degree Navigator network discovery.

    python scripts/discover_dn.py [--max-minutes 60]

You log in yourself (NetID + Duo - never typed by this script), open the main
audit page and expand every requirement, then close the window. The HAR goes
to data/private/har/. The DN session is deliberately NOT saved: per-student DN
data may enter CoursePilot only as a PDF the student uploads (trust boundary
3), so there is no reason to keep a DN credential.
"""

from _discovery_browser import capture

if __name__ == "__main__":
    def signed_in(page) -> bool:
        # On a Degree Navigator host and no longer at CAS/Duo.
        host = page.url.split("/")[2] if "://" in page.url else ""
        return host.endswith("dn.rutgers.edu") and "cas." not in host

    capture(
        "DN", "https://dn.rutgers.edu/", signed_in=signed_in, state_file=None,
        instructions=("Log in with NetID + Duo yourself, open your main audit page, and expand "
                      "every requirement."))
