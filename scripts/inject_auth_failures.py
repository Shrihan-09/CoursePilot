"""Phase 7.1 failure injection: break sign-in on purpose, prove the tests notice.

Each fault edits one file; the web auth tests (vitest: config, scopes, PKCE
flow against a real local provider, proxy, middleware, cookies) and the API
identity tests (pytest, real PostgreSQL) run; the file is restored whatever
happens. DETECTED = a test failed. MISSED is reported, never hidden.

Usage:  python scripts/inject_auth_failures.py [--check] [fault ...]
"""

from __future__ import annotations

# Files are read and written with newline="" so line endings survive
# byte-for-byte (on Windows, text mode would turn LF files into CRLF).

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
CONFIG = "frontend/src/lib/auth/config.ts"
OIDC = "frontend/src/lib/auth/oidc.ts"
SESSION = "frontend/src/lib/auth/session.ts"
PROXY = "frontend/src/app/api/cp/[...path]/route.ts"
MIDDLEWARE = "frontend/src/middleware.ts"
LOGOUT = "frontend/src/app/auth/logout/route.ts"
AUTH = "backend/app/api/auth.py"
SECURITY = "backend/app/api/security.py"
ACCOUNTS = "backend/app/services/accounts.py"
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot_test")

FAULTS = {
    "A": ("an extra scope is requested (profile)", [(CONFIG,
          'export const OIDC_SCOPES = ["openid", "email"] as const;',
          'export const OIDC_SCOPES = ["openid", "email", "profile"] as const;')]),
    "B": ("offline access requested (a refresh token: more than identity)", [(OIDC,
          '  url.searchParams.set("nonce", txn.nonce);\n',
          '  url.searchParams.set("nonce", txn.nonce);\n  url.searchParams.set("access_type", "offline");\n')]),
    "C": ("development sign-in allowed in production", [(CONFIG,
          '    if (production) return disabled("development sign-in is refused in production");\n',
          '')]),
    "D": ("missing OIDC configuration falls back instead of failing closed", [(CONFIG,
          '    if (missing.length) return disabled(`missing configuration: ${missing.join(", ")}`);',
          '    if (false) return disabled("");')]),
    "E": ("open redirect: protocol-relative returnTo accepted", [(CONFIG,
          '  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\\\")) return fallback;',
          '  if (!value.startsWith("/")) return fallback;')]),
    "F": ("callback state not checked (CSRF)", [(OIDC,
          "  const params = oauth.validateAuthResponse(as, c, callbackUrl, txn.state);",
          "  const params = oauth.validateAuthResponse(as, c, callbackUrl, oauth.skipStateCheck);")]),
    "G": ("PKCE verifier not sent", [(OIDC,
          "    as, c, clientAuth(config), params, config.redirectUri, txn.codeVerifier, httpOptions(config),",
          "    as, c, clientAuth(config), params, config.redirectUri, oauth.nopkce, httpOptions(config),")]),
    "H": ("session cookie readable by page script (not httpOnly)", [(SESSION,
          '  return { httpOnly: true, secure, sameSite: "lax" as const, path: "/", maxAge };',
          '  return { httpOnly: false, secure, sameSite: "lax" as const, path: "/", maxAge };')]),
    "I": ("proxy forwards a page-supplied Authorization header", [(PROXY,
          'const FORWARDED_REQUEST_HEADERS = ["content-type", "accept"];',
          'const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "authorization"];')]),
    "J": ("proxy allows path traversal segments", [(PROXY,
          '  if (!path.length || path.some((s) => !SEGMENT.test(s) || s === "." || s === "..")) {',
          '  if (!path.length) {')]),
    "K": ("protected routes not protected", [(MIDDLEWARE,
          '  if (isProtected(pathname) && !session) {',
          '  if (false) {')]),
    "L": ("logout leaves the session in place", [(LOGOUT,
          "  response.cookies.delete(SESSION_COOKIE);\n", "")]),
    "M": ("API: iat no longer required", [(AUTH,
          '                options={"require": ["exp", "iat", "iss", "aud", "sub"]},\n'
          '            )\n        except Exception as exc:\n'
          '            # Only the exception CLASS is logged.',
          '                options={"require": ["exp", "iss", "aud", "sub"]},\n'
          '            )\n        except Exception as exc:\n'
          '            # Only the exception CLASS is logged.')]),
    "N": ("API: institution membership read from the email domain", [(AUTH,
          "    hd = claims.get(settings.auth_institution_claim)\n",
          "    hd = claims.get(settings.auth_institution_claim) or (claims.get('email') or '@').split('@')[1]\n"),
          (AUTH, 'IDENTITY_CLAIMS = ("hd", "email_verified")',
           'IDENTITY_CLAIMS = ("hd", "email_verified", "email")')]),
    "O": ("API: an unverified email still counts as a member", [(AUTH,
          '    if claims.get("email_verified") is False:\n        return InstitutionStatus.NOT_VERIFIED\n',
          "")]),
    "P": ("API: accounts not issuer-scoped (provider change merges people)", [(ACCOUNTS,
          "        UserAccount.identity_issuer == principal.issuer,\n", "")]),
    "Q": ("API: non-members get an account before being refused", [(SECURITY,
          "            account = resolve_account(session, authenticated)\n",
          "            account = resolve_account(session, authenticated)\n"
          "            session.commit()\n"
          "            if settings.auth_require_institution and institution != InstitutionStatus.VERIFIED:\n"
          "                raise HTTPException(status_code=403, detail='no')\n"),
          (SECURITY,
          "    if settings.auth_require_institution and institution != InstitutionStatus.VERIFIED:\n"
          "        raise HTTPException(\n",
          "    if False:\n        raise HTTPException(\n")]),
    "R": ("API: the email address is carried past verification", [(AUTH,
          'IDENTITY_CLAIMS = ("hd", "email_verified")',
          'IDENTITY_CLAIMS = ("hd", "email_verified", "email")')]),
}


def _apply(letter: str) -> dict:
    _, edits = FAULTS[letter]
    originals: dict[pathlib.Path, str] = {}
    for rel, old, new in edits:
        path = ROOT / rel
        originals.setdefault(path, path.read_text(encoding="utf-8", newline=""))
        current = path.read_text(encoding="utf-8", newline="")
        eol = "\r\n" if "\r\n" in current else "\n"
        old, new = old.replace("\n", eol), new.replace("\n", eol)
        if current.count(old) != 1:
            for p, text in originals.items():
                p.write_text(text, encoding="utf-8", newline="")
            raise LookupError(f"pattern not found exactly once in {rel}: {old[:60]!r}")
        path.write_text(current.replace(old, new), encoding="utf-8", newline="")
    return originals


def _run(cmd: list[str], cwd: pathlib.Path, env: dict) -> tuple[bool, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, check=False,
                          timeout=900, shell=os.name == "nt" and cmd[0] == "npx")
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    summary = next((ln for ln in reversed(lines) if "passed" in ln or "failed" in ln), lines[-1:] or [""])
    return proc.returncode != 0, str(summary).strip()


def run(letter: str) -> tuple[bool, str]:
    try:
        originals = _apply(letter)
    except LookupError as exc:
        return False, f"INJECTION FAILED: {exc}"
    try:
        env = {**os.environ, "DATABASE_URL": TEST_DB, "DATABASE_URL_SYNC": TEST_DB,
               "TEST_DATABASE_URL": TEST_DB}
        web_failed, web = _run(["npx", "vitest", "run"], ROOT / "frontend", env)
        api_failed, api = _run([PY, "-m", "pytest", "tests/test_auth_identity.py",
                                "tests/test_auth_accounts.py", "-q", "-x", "-p", "no:cacheprovider"],
                               ROOT / "backend", env)
        return web_failed or api_failed, f"web: {web} | api: {api}"
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8", newline="")


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--check"]:
        for letter in args[1:] or list(FAULTS):
            try:
                for path, text in _apply(letter).items():
                    path.write_text(text, encoding="utf-8", newline="")
                print(f"{letter:3} ok")
            except LookupError as exc:
                print(f"{letter:3} BROKEN {exc}")
        return
    for letter in args or list(FAULTS):
        detected, detail = run(letter)
        print(f"{letter:3} {'DETECTED' if detected else 'MISSED  '} {FAULTS[letter][0]}\n"
              f"    {detail}", flush=True)


if __name__ == "__main__":
    main()
