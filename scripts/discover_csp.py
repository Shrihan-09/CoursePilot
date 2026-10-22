"""Phase 7.3 Step 2: MANUAL Course Schedule Planner network discovery.

    python scripts/discover_csp.py [--max-minutes 60]

You log in yourself (NetID + Duo in the browser - never typed by this script),
then click through: select a term, browse a subject, open a course, open
section details, build a schedule, check open/closed status, and every other
field CSP shows. Close the window when done. The HAR goes to
data/private/har/; the session state to data/private/csp_state.json
(gitignored; trust boundary 2). Nothing is printed except file names and sizes.
"""

from _discovery_browser import capture

if __name__ == "__main__":
    def signed_in(page) -> bool:
        # Inside the CSP application: on sims.rutgers.edu/csp/ and past the
        # "Login - Course Schedule Planner" page (CAS/Duo hosts never match).
        return ("sims.rutgers.edu/csp/" in page.url
                and not page.title().lower().startswith("login"))

    capture(
        "CSP", "https://sims.rutgers.edu/csp/", signed_in=signed_in, state_file="csp_state.json",
        instructions=("Log in with NetID + Duo yourself, then: select a term, browse a subject, "
                      "open a course, open section details, build a schedule, check open/closed "
                      "status, and open every other view CSP offers."))
