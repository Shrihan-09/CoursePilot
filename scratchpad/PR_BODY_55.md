# Phase 5.5 — Student Linking, Provisioning & Audit Trail

## Summary

Closes Phase 5.4's honest gap: authentication worked and ownership was modeled, but every account was unlinked and `409` was a truthful answer rather than a finished feature.

```
verified identity  ->  administrator verifies the person out of band
                   ->  student.user_id + StudentLinkEvent, one transaction
```

The Degree Engine is untouched and returns identical results before and after linking.

## Part 1 — what the existing student data actually proves

The tempting shortcut was `Student.external_ref == principal.subject`. It would have worked in the demo. Investigated first:

| question | finding |
|---|---|
| how many students exist? | **one**, `external_ref = 'smoke-1'`, 11 course rows |
| who writes `external_ref`? | test fixtures and the smoke-test path |
| does production code create `Student` rows? | **no code path does** |
| exposed in any API response? | **no** |
| derived from a Rutgers identifier? | **no** — it is an ingestion label |

It proves nothing about a person. **Phase 5.3's `student_ref` is not reintroduced as trust anywhere in this phase.**

## Choosing the linking model

| model | verdict |
|---|---|
| **A. administrator-assisted** | **chosen** |
| B. one-time code | deferred |
| C. IdP claim matching | unavailable |
| D. hybrid | premature |

**C is unavailable, not unimplemented.** It is the right long-term answer, but Rutgers SSO is unverified, Rutgers publishes CAS/Shibboleth rather than OIDC, and no student-number claim is released to this project.

**B is deferred for a specific reason.** A code is only as trustworthy as the channel delivering it, and there is no channel — no email, no SMS, no registrar feed. An administrator would generate the code and hand it to the person *they just verified in person*. It adds storage, hashing, expiry, replay and brute-force surface and **zero additional verification**.

**A is chosen** because the verification is real and simply happens outside the software. No secret to store, no channel to trust, no brute-force surface — because nothing is being guessed.

## Administrative authority

```sql
user_account.is_admin  BOOLEAN NOT NULL DEFAULT false
```

A column, not a token claim: a claim travels inside a credential and outlives its own revocation. Re-read on every request, settable by no request field, false by default. Phase 5.4 dropped `groups`/`is_admin` from the claim subset for exactly this reason.

## The API

```
POST /api/v1/admin/students/{student_id}/link    {identity_provider, external_subject, reason?}
POST /api/v1/admin/students/{student_id}/unlink  {reason?}
```

The admin names the person by **identity**, never by account id — so no step requires listing accounts, and no step can be turned into a listing.

A student id appears in the path and **grants nothing**. This is not Phase 5.3's flaw returning: there, *naming* a student granted access. Here authority comes from `is_admin`, checked before the lookup, so a non-admin gets an identical 403 whether the id is real or fabricated.

Responses are thin — `{student_id, linked, event_recorded}`. An administrative response should confirm the operation, not double as a reader for other people's data.

| outcome | status |
|---|---|
| no credential | 401 |
| authenticated, not an administrator | 403 |
| student does not exist | 404 |
| named identity has no account | 404 |
| already owned / account already owns one | 409 |
| unlinking something not linked | 409 |

An unknown identity is a 404 rather than a provisioning step. An admin typing a NetID must not create an account, or the table records what an operator believed instead of what an IdP attested.

## Concurrency — two races, two mechanisms

| race | mechanism |
|---|---|
| two students, one account | `UNIQUE (student.user_id)` |
| **one student, two accounts** | `SELECT … FOR UPDATE` on the student row |

The second matters: a unique index constrains *across* rows and says nothing about one row's history. Both transactions read `user_id IS NULL`, both update, and the second silently reassigns the record — passing every application check and violating no constraint.

## Audit trail

```sql
student_link_event(student_id, user_account_id, performed_by_id, action, reason, created_at)
  -- all three FKs ON DELETE RESTRICT; CHECK (action IN ('linked','unlinked'))
```

Append-only. Written in the **same transaction** as the ownership change. `RESTRICT` is load-bearing: an account named by the history cannot be deleted even after it owns nothing — an audit trail a later action can erase is not an audit trail.

**These are not academic facts.** The engine never reads them, and a test asserts the engine package mentions none of `UserAccount`, `is_admin`, `StudentLinkEvent` or `Principal`. Linking changes who may ask; it does not change what is true.

**Unlinking deletes nothing.** Only `user_id` is cleared. `StudentCourse` cascades from `Student`, so implementing unlink as a delete would destroy a transcript on the first correction of a typo.

## Development bootstrap — a command, not an endpoint

```
python -m app.cli.dev_bootstrap grant-admin --provider dev --subject alice
```

A route that must never run in production is still a route in production: reachable, fuzzable, and one misread environment variable from granting admin to a stranger. A command has no listener, runs only where someone already holds shell and database credentials, and refuses outright when `COURSEPILOT_ENV` is production.

## Rate limiting

10 linking operations per identity per minute, charged **after** authorization so a rejected non-admin cannot exhaust an administrator's allowance. Not a brute-force control — the workflow contains no secret — a **blast-radius** control: reusing the 60-request budget would let one leaked admin token reassign sixty transcripts a minute.

## Migration

`d35eb3a64b5d -> 0e8148bec496`. Adds `is_admin` and `student_link_event`; **backfills nothing**.

Verified against a **copy** of the real development database (never the source):

```
before   1 student, 11 course rows, 2 accounts
after    1 student, 11 course rows, 2 accounts
         is_admin = false for both, 0 link events, student still unlinked
upgrade -> downgrade -> upgrade   data identical
alembic check                     no new upgrade operations
```

## Tests

```
SQLite      554 passed,  66 skipped    (unchanged from 5.1–5.4)
PostgreSQL  619 passed,   1 skipped    (unchanged from 5.1–5.4)
Backend     132 passed,   2 skipped    (was 99; +33)
```

The 2 backend skips remain opt-in: the live AI provider and the live SSO smoke test.

Authorization tests override only the principal, so `require_admin` genuinely runs. The sharpest grants admin, makes a request, revokes between requests, and repeats it: **409 then 403**, with nothing about the caller's credential having changed.

## Performance

```
no credential                       401     0.96 ms
authenticated non-admin             403    18.78 ms
admin, student not found            404    40.18 ms
admin link (incl. per-run db reset) 200    73.08 ms
explanation, linked account         200   270.11 ms
```

The explanation endpoint is the Phase 5.4 comparison point (~320 ms warm) and is unchanged, still dominated by the ~230 ms BM25 rebuild.

## Known limitations

**Genuine:**
1. **Rutgers live SSO is still unverified.** Phase 5.5 changes nothing here — the admin-assisted model was chosen precisely so it does not depend on a claim Rutgers does not release.
2. **Linking depends on a human verifying correctly.** The software records the decision; it does not make it. A careless administrator is an unhandled failure mode, and the audit trail is the mitigation — it makes the mistake attributable, not impossible.
3. **No self-service linking**, so onboarding does not scale beyond a small cohort. Model C is the fix and is blocked on Rutgers.
4. **No admin UI** — two JSON endpoints and a CLI.
5. **Rate limiting remains per-process and in-memory**, so not a production control.
6. **No audit-read API.** Events are queryable only via SQL.
7. **`is_admin` is a single flag**, not a role model.

**Intentionally deferred:** one-time codes, self-service claiming, bulk linking, admin-initiated account disabling, audit export.

## Unchanged

Allocation, category allocation, the optimizer, baseline semantics, sharing policy, course eligibility, BM25 ranking, `CourseDocument`, Phase 5.1 explanation facts, Phase 5.2 provider behaviour, Phase 5.3 fallback and Phase 5.4 token validation — proven by identical suite counts (554 / 619).

---

PR not created because `gh` is unavailable.

Compare:
https://github.com/Shrihan-09/CoursePilot/compare/main...feature/phase-5-5-student-linking?expand=1

🤖 Generated with [Claude Code](https://claude.com/claude-code)
