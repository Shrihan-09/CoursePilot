# Security: secrets and keys

How Coursenav keeps credentials out of Git and out of logs, what to do when a
key leaks, and how key handling changes at deploy time.

Scope: API keys (OpenAI, Anthropic, APNs), OIDC configuration, the local
database password, and private student material. Authentication itself is
covered in [AUTHENTICATION.md](AUTHENTICATION.md).

---

## 1. Where secrets live

| Secret | Local development | Deployed |
|---|---|---|
| `OPENAI_API_KEY` | `.env` at the repository root (gitignored) | Platform secret store, then cloud identity (see §5) |
| `ANTHROPIC_API_KEY` | `.env`, only if a `*_PROVIDER` is `anthropic` | Platform secret store |
| `APNS_PRIVATE_KEY` / `APNS_KEY_PATH` | Not used locally (`PUSH_PROVIDER=none`) | Platform secret store or a mounted file |
| `POSTGRES_PASSWORD` | `.env`, local Docker database only | Managed database credentials |
| Real transcripts, DN/WebReg exports, HAR captures, browser state | `C:\coursepilot-private` or `data/private/`, never in the repository tree | Not deployed |

Rules:

- **`.env.example` holds names and placeholders only.** Copy it to `.env` and
  fill in real values there. Never put a real value back in `.env.example`.
- **The key is server-side only.** No request field selects or carries a
  provider key, and the frontend never sees one. `NEXT_PUBLIC_*` variables are
  shipped to the browser, so a secret must never get that prefix.
- **Keys never reach logs.** Startup messages name settings, never values.
  `app/core/logging.py` adds `SecretRedactionFilter` to every log record. It
  scrubs the configured key values and anything shaped like `sk-…`,
  `Bearer …`, `Authorization:`, `x-api-key:` or `cookie:`. The `openai`,
  `anthropic`, `httpx` and `httpcore` loggers are held at WARNING because at
  DEBUG they print request options, including headers. Call sites must still
  never log request headers; the filter is a safety net, not a substitute.

## 2. Guardrails

| Layer | What it stops | Where |
|---|---|---|
| `.gitignore` | `.env`, `.env.*` (except `.env.example`), `*.har`, `*state*.json`, `data/private/`, `coursepilot-private/`, `*.pem`, `*.key`, `*.p8`, `secrets/` | `.gitignore` |
| Pre-commit path guard | Staged files with those names, even if force-added | `scripts/check_private_files.py` |
| Pre-commit content scan | Secrets inside any staged change (gitleaks, values redacted) | `scripts/hooks/pre-commit` |
| Startup check | Booting with an OpenAI provider selected but no key | `require_openai_key` in `app/core/config.py` |
| Log redaction | A key or header that reaches a log line by mistake | `SecretRedactionFilter` in `app/core/logging.py` |

### Enable the hooks (once per clone)

```bash
# Windows: winget install Gitleaks.Gitleaks   macOS: brew install gitleaks
git config core.hooksPath scripts/hooks
```

The hook fails closed: if gitleaks is not installed, the commit is refused
with install instructions. Do not bypass it with `--no-verify`.

### Scan the whole history

```bash
gitleaks git . --redact --report-format json --report-path <somewhere-outside-the-repo>/gitleaks.json
```

Always pass `--redact`, and write reports outside the repository: a report
holds the match location, and without `--redact` it holds the value too.

### Reviewed false positives

`.gitleaksignore` lists findings that were checked and are not credentials, by
fingerprint (`commit:file:rule:line`, never a value), each with a one-line
reason. Add an entry only after confirming the match is not a real secret. A
real secret is never "ignored": rotate it (§3).

The first full-history scan (343 commits, 2026-11-10) found two matches, both
false positives: a test-fixture `action_token` placeholder and a
`uuid4()` notification lease token.

### Fail fast on a missing OpenAI key

If `LLM_PROVIDER`, `EXPLANATION_PROVIDER` or `ASSISTANT_PROVIDER` is `openai`
and `OPENAI_API_KEY` is empty, the API refuses to start:

```
CRITICAL app.main: startup_refused: OPENAI_API_KEY is not set, but ASSISTANT_PROVIDER=openai. ...
```

`ASSISTANT_PROVIDER` defaults to `openai`, so a fresh `.env` needs a key. To
work offline, choose providers that need none: `ASSISTANT_PROVIDER=fake`,
`EXPLANATION_PROVIDER=none`, `LLM_PROVIDER=echo`. The test suite already does
this.

## 3. Rotating a key

Do this on any suspicion: a key in a commit (pushed or not), a screenshot, a
paste, a log line or a shared terminal. Also rotate on a fixed schedule, every
90 days.

**OpenAI**

1. In the OpenAI dashboard, open **API keys** for the Coursenav project and
   **create a new key** first. Use a project-scoped key with only the model
   permissions the app uses.
2. Put the new key in `.env` locally, or in the platform secret store when
   deployed, and restart the API. It refuses to start if the value is empty.
3. Check that the assistant answers. The startup log shows the provider
   names, never the key.
4. **Revoke the old key** in the dashboard. Revoke it right away if it leaked;
   otherwise wait until the new one is confirmed working.
5. Review **Usage** for the leak window and look for traffic you don't
   recognize.

**If the key was committed:** rotating comes first. Rewriting history does not
un-leak a pushed key, because clones, forks, caches and the GitHub event
stream may already hold it. After rotating, decide whether to purge history
with `git filter-repo`. That requires a coordinated force-push, which is never
done to `main` without the owner.

**Anthropic / APNs / database**: same order: issue the new credential, deploy
it, verify, revoke the old one. For APNs, revoke the `.p8` key in the Apple
Developer portal and update `APNS_KEY_ID`.

## 4. OpenAI budget cap

A leaked or runaway key should cost a bounded amount. Set both layers.

**At OpenAI (the hard stop)**, in the organization's billing / limits settings:

- Set a **monthly budget** for the Coursenav project at the expected spend
  plus a margin, and a **notification threshold** at about 50% and 80%.
- Use a **separate project** for Coursenav, so its key and budget are
  isolated from anything else on the account.
- Restrict the project's allowed models to the ones configured
  (`OPENAI_MODEL`, `ASSISTANT_MODEL`, `ASSISTANT_ESCALATION_MODEL`).

**In the app (per-user and per-turn ceilings)**, already in `.env.example`:

| Setting | Bounds |
|---|---|
| `ASSISTANT_DAILY_MESSAGE_LIMIT` | Messages per student per day |
| `ASSISTANT_TURN_TOKEN_BUDGET`, `ASSISTANT_MAX_OUTPUT_TOKENS` | Tokens per assistant turn |
| `ASSISTANT_MAX_TOOL_CALLS`, `ASSISTANT_TIMEOUT_SECONDS` | Work per turn |
| `ASSISTANT_ESCALATION_ENABLED` | Whether the larger model is ever used |
| `RATE_LIMIT_MODEL_CALLS` / `RATE_LIMIT_WINDOW_SECONDS` | Model calls per client per window |
| `EXPLANATION_MAX_OUTPUT_TOKENS`, `EXPLANATION_MAX_PROVIDER_CALLS` | Explanation-layer spend |

The in-app limits bound normal use. Only the OpenAI-side budget bounds a
stolen key, because a thief calls OpenAI directly.

## 5. Deploy plan: identity-based provider, no static key

A long-lived API key in an environment variable is acceptable for local
development and a first deploy. The deployed target is **no static model key
at all**: the API authenticates to the model provider with the identity of the
workload it runs on. The cloud provider issues short-lived credentials, so
there is nothing to copy, commit or rotate by hand.

| Host | Provider | Identity mechanism |
|---|---|---|
| Azure | Azure OpenAI | Managed identity, role *Cognitive Services OpenAI User* on the resource; key auth disabled on the resource |
| AWS | Amazon Bedrock | IAM role for the task or service (ECS task role / IRSA), `bedrock:InvokeModel` on the specific model ARNs only |
| Google Cloud | Vertex AI | Attached service account (Workload Identity), role *Vertex AI User* scoped to the project |

Pick the one matching where the API is hosted. The steps are the same:

1. **Add a provider adapter** beside `app/llm/providers/openai.py` that builds
   its client from the platform's default credential chain
   (`DefaultAzureCredential`, the boto3 default chain, or Google ADC) and
   implements the same `LLMProvider` protocol. No call site changes, because
   every caller already goes through the protocol.
2. **Configure by name, not by secret.** The new settings are a resource
   endpoint, a deployment or model id, and a region. None of them are secret.
   Extend `require_openai_key` into a per-provider check, so startup still
   fails fast when the endpoint or model is missing.
3. **Grant least privilege.** Allow invoke on only the models in use, in only
   the deployed environment's project or subscription.
4. **Set the budget at the cloud account** (Azure Cost Management budget, AWS
   Budgets, GCP billing budget) with alerts, mirroring §4.
5. **Cut over and remove the key.** Switch `*_PROVIDER` to the new adapter,
   verify, then delete `OPENAI_API_KEY` from the secret store and revoke it at
   OpenAI.

Until that cutover, the deployed key lives only in the platform's secret store
(never in an image, a compose file or the repository), is project-scoped, has
an OpenAI-side budget, and is rotated every 90 days.

## 6. Reporting

If you find a secret in this repository or its history, don't open a public
issue. Tell the maintainer privately, and rotate the credential first if it's
yours.
