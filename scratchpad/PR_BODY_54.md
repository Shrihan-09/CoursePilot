# Phase 5.4 — Real Authentication & Account Ownership

## Summary

Replaces Phase 5.3's asserted development identity with **verified** authentication and **database-enforced** ownership.

```
External Identity Provider -> AuthenticatedPrincipal -> UserAccount.id -> Student -> academic data
```

The Degree Engine is untouched, and the one existing student record was preserved **without inventing an owner for it**.

Based on `main`, which now contains Phase 5.3.

## Authentication

Standards-based JWT verification via **PyJWT** (no hand-rolled cryptography). The project supplies policy:

| check | why |
|---|---|
| signature | the only thing making any other claim meaningful |
| asymmetric-only algorithm allow-list | rejects `alg: none` and HMAC confusion by configuration, not by trusting the token's own declaration |
| `iss` | a valid token from another issuer is not valid here |
| `aud` | a token minted for another service must not be replayable |
| `exp` / `nbf` | expiry is the only revocation most IdPs offer |
| `sub` required | an identity with no subject is not an identity |
| 60 s clock skew | generous skew extends a revoked token's life |

**Everything fails closed.** No configuration, incomplete OIDC settings, an unknown provider, or dev auth in production all yield *no verifier*, and every credential is refused.

Only a minimal claim subset is carried forward — `groups`, `is_admin` and friends are dropped, because a claim that reaches the application is one something may eventually trust.

## Account model

```sql
user_account(id, identity_provider, external_subject, disabled_at, ...)
  UNIQUE(identity_provider, external_subject)

student.user_id  NULLABLE, UNIQUE, FK -> user_account(id) ON DELETE RESTRICT
```

External identity is kept **separate** from internal identity: a provider's `sub` can change (IdP migration, recreated account) while `UserAccount.id` must not, because rate-limit keys and logs hang off it. Making `sub` a primary key would weld CoursePilot's graph to one vendor.

Cardinality is reasoned: nullable because an unlinked record is a real state; unique because one account must not accumulate academic records. Relaxing that later is a dropped constraint; adding it later to violating data is much worse.

Table is `user_account` — `user` is reserved in PostgreSQL.

## Authorization

The endpoint queries `student.user_id == account.id`. **No code path reaches a record by anything a client supplied**, which makes cross-user access impossible rather than merely checked.

Provisioning and linking are deliberately separate:

```
verified token -> provision UserAccount     automatic, safe
UserAccount    -> link to a Student         NOT automatic
```

A verified identity proves *who someone is*, not *which academic record is theirs*. A `sub` is an opaque provider key; `external_ref` is an ingestion label. Matching them would be a guess wearing the costume of a lookup.

So a new account exists and owns nothing, and the API says so with **409** — not 404 (which would claim the user's own data is missing) and not 403 (which would imply refusal).

## Migration

`d0821611331c -> d35eb3a64b5d`. Adds only; **backfills nothing** — the existing student's `external_ref` is not evidence of ownership. `NOT NULL` deliberately not enforced yet.

Tested: fresh upgrade → downgrade → re-upgrade, plus an upgrade against a **copy of the real dev database** (1 student, 11 course rows preserved, correctly unlinked). Constraints proven by the database rejecting writes: duplicate identity, second student per account, account deletion while owning a record.

**Defect this found:** SQLAlchemy's default relationship behaviour nulls the child FK *before* deleting the parent, so deleting an account silently orphaned an academic record instead of being refused. `passive_deletes="all"` defers to the database and makes `RESTRICT` real.

## Tests

```
PostgreSQL:   619 passed,  1 skipped    (unchanged from 5.1/5.2/5.3)
SQLite:       554 passed, 66 skipped    (unchanged from 5.1/5.2/5.3)
Backend/API:   99 passed,  2 skipped    (was 70; +29)
```

The 2 backend skips are both opt-in: the live AI provider and the live SSO smoke test.

**Three ownership tests were silently skipping** on missing fixture data — the three that mattered most. They now build their own program chain, and the first real run found the `passive_deletes` defect. The `db` marker the conftest already described is now registered, so an unregistered mark is no longer just a warning.

API tests use a **dependency override** for the principal rather than weakening authentication: the route still depends on the real dependency, and the suite still needs no database.

## Live SSO

```
Test-token verification: VERIFIED   (real RS256 tokens, real PyJWT path)
Rutgers live SSO:        NOT VERIFIED
```

Researched against current official documentation: Rutgers IT publicly documents **CAS, Shibboleth (SAML) and LDAP/RAD** with an "SSO Decision Flow", publishes **no OIDC discovery endpoint or self-service client registration**, and requires engaging Rutgers IT for approval and credentials.

A Rutgers deployment would need an OIDC bridge in front of CAS/Shibboleth, or a SAML verifier implementing the same `TokenVerifier` protocol — which is why that protocol exists.

## Security

- **Rate limiting** now keys on the stable `UserAccount.id`, never the provider subject, an email, or a student reference. Budgets unchanged (60 requests / 10 model calls per identity per minute).
- **Logging** carries a hashed account handle. Never: tokens, `Authorization` headers, API keys, JWT payloads, prompts, model responses, academic content.
- **Development auth** requires `AUTH_PROVIDER=dev` *and* `DEV_AUTH_ENABLED=true` (both off by default), is refused in production, and issues principals under the `dev` provider — a different namespace from `oidc`, so it could not impersonate a real user even if it leaked.
- **Token handling**: only the exception *class* is logged on failure; SDK errors can echo the credential.

## Performance

```
no credential           401   rejected before account or audit work
authenticated, unlinked 409
authenticated, linked   200   warm 317 ms
```

Phase 5.3 baseline was ~320 ms warm. Authentication, account resolution and ownership lookup add **no measurable overhead**; the ~230 ms BM25 rebuild still dominates and remains deliberately uncached.

## Deterministic regression

Allocation, category allocation, the optimizer, baseline semantics, sharing policy, course eligibility, BM25 ranking, `CourseDocument`, Phase 5.1 explanation facts, Phase 5.2 provider behaviour and Phase 5.3 fallback are all unchanged — proven by identical suite counts (554 / 619).

`alembic check`: clean.

## Known limitations

**Genuine:**
1. **Rutgers live SSO is unverified** and cannot be verified from this environment.
2. **No production linking mechanism.** Every account starts unlinked; 409 is an honest response, not a finished feature.
3. Rate limiting is still per-process and in-memory.
4. No token revocation beyond expiry.
5. No admin surface for linking, disabling or auditing accounts.

**Intentionally deferred:** account deletion/merge, provider-subject migration, multi-programme students, and any password-based login — CoursePilot should not become its own identity provider.

---

PR not created because `gh` is unavailable.

Compare:
https://github.com/Shrihan-09/CoursePilot/compare/main...feature/phase-5-4-real-auth-accounts?expand=1

🤖 Generated with [Claude Code](https://claude.com/claude-code)
