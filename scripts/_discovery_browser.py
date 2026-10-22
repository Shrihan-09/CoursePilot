"""Shared, MANUAL network-discovery capture for Phase 7.3 (PHASE_7_3_PLAN.md §2, §6).

Opens the developer's installed Chrome, HEADED, in two phases:

1. LOGIN - NOT recorded. The developer signs in by hand (NetID + Duo are never
   automated - this script never types into any page). The script only waits
   until the browser is inside the target application.
2. RECORDING - a fresh context with the signed-in session records every
   response to a HAR in <private>/har/ while the developer clicks through.
   CAS/Duo login traffic therefore never reaches a HAR.

The capture ends when the developer closes the window (or after --max-minutes).

Private material - HARs hold cookies/tokens; session state is a live
credential - is written ONLY under COURSEPILOT_PRIVATE_DIR (default
data/private/; keep it outside OneDrive), never printed, logged or committed.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import time
from collections.abc import Callable
from datetime import UTC, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent


def private_dir() -> pathlib.Path:
    path = pathlib.Path(os.environ.get("COURSEPILOT_PRIVATE_DIR") or ROOT / "data" / "private")
    (path / "har").mkdir(parents=True, exist_ok=True)
    return path


def _open_pages(context) -> list:
    return [p for p in context.pages if not p.is_closed()]


def _pump(page) -> None:
    """Wait ~1 s THROUGH Playwright. The sync API only processes browser events
    (a page closing) during a Playwright call - a bare time.sleep() never sees
    the window close, which is how the first CSP capture hung."""
    try:
        page.wait_for_timeout(1000)
    except Exception:  # noqa: BLE001 - the page closed during the wait: re-check
        time.sleep(0.2)


def capture(name: str, start_url: str, *, signed_in: Callable, state_file: str | None,
            instructions: str) -> None:
    """`signed_in(page) -> bool`: True once the page is inside the application."""
    from playwright.sync_api import sync_playwright

    p = argparse.ArgumentParser(description=f"Manual {name} network discovery")
    p.add_argument("--max-minutes", type=float, default=60)
    p.add_argument("--login-minutes", type=float, default=10)
    p.add_argument("--label", default="", help="extra word in the HAR filename, e.g. redo")
    args = p.parse_args()

    private = private_dir()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    label = f"-{args.label}" if args.label else ""
    har = private / "har" / f"{name.lower()}{label}-{stamp}.har"
    state = private / state_file if state_file else None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=False)

        # Phase 1: login, unrecorded.
        login = browser.new_context(storage_state=str(state) if state and state.exists() else None,
                                    viewport={"width": 1400, "height": 900})
        page = login.new_page()
        page.goto(start_url)
        print(f"[{name}] LOGIN (not recorded): sign in yourself in the browser window.", flush=True)
        deadline = time.monotonic() + args.login_minutes * 60
        while time.monotonic() < deadline:
            live = _open_pages(login)
            if not live:
                print(f"[{name}] window closed before sign-in; nothing recorded.")
                login.close()
                browser.close()
                return
            try:
                if signed_in(live[0]):
                    break
            except Exception:  # noqa: BLE001 - mid-navigation
                pass
            _pump(live[0])
        else:
            print(f"[{name}] sign-in not completed in {args.login_minutes} min; nothing recorded.")
            browser.close()
            return
        session = login.storage_state(path=str(state)) if state else login.storage_state()
        login.close()

        # Phase 2: recording starts only now, already signed in.
        context = browser.new_context(storage_state=session, record_har_path=str(har),
                                      record_har_content="embed",
                                      viewport={"width": 1400, "height": 900})
        context.new_page().goto(start_url)
        print(f"[{name}] RECORDING started (signed in). {instructions}")
        print(f"[{name}] Close the browser window when you are finished.", flush=True)
        deadline = time.monotonic() + args.max_minutes * 60
        while time.monotonic() < deadline:
            live = _open_pages(context)
            if not live:
                break                        # the developer closed the last window
            _pump(live[0])
        if state:
            context.storage_state(path=str(state))
        context.close()          # flushes the HAR
        browser.close()
    size = har.stat().st_size if har.exists() else 0
    print(f"[{name}] HAR saved: {har.name} ({size // 1024} KB) in the private directory. "
          f"Session state {'saved' if state else 'NOT saved (by design)'}.")


__all__ = ["capture", "private_dir"]
