# Spike results — Plan 1, Task 7

Run on 2026-10-15 against the final Plan 1 code (Tasks 1–6 plus the final-review fix wave). Dev benches: Frappe v15 (Python 3.12) and v16 (Python 3.14), each served by `bench serve --proxy` and exposed over HTTPS with a Cloudflare quick tunnel (`host_name` set to the tunnel origin).

| # | Risk (spec §13) | v15 | v16 |
|---|---|---|---|
| 1 | `/.well-known/oauth-*` served (ours on v15, core on v16) | **PASS** — `page_renderer` receives both documents; issuer, endpoints and the 401 `resource_metadata` URL all use the `https://` tunnel origin (`dev/oauth_smoke.py`, all checks, through the tunnel) | **PASS** — core serves both documents; with `bench serve --proxy` the origin is `https://`. Without `--proxy` v16 core builds `http://` URLs from the request, so a proxy that does not forward `X-Forwarded-Proto` breaks discovery (production gunicorn behind nginx handles this) |
| 2 | Public client + PKCE S256 token exchange; wrong verifier refused | **PASS** — shim registers a public client, S256 code exchange returns a token, wrong verifier → refused, `refresh_token` grant works | **PASS** — core registration (scope `all` only) + same flow; wrong verifier refused; refresh works |
| 3 | Frappe Cloud nginx passes `/.well-known/oauth-*` | **DEFERRED to Plan 4** — no Frappe Cloud site available yet | **DEFERRED to Plan 4** |
| 4 | Claude custom connector: OAuth + tools/list + gated create_record | **PASS** — the owner added the connector in Claude: sign-in and consent worked, Claude listed the 8 tools, `submit_plan` → owner said go → `approve_plan` → `create_record` created a ToDo owned by Administrator (plan `guq8t1fpcb` Approved; the ToDo row confirmed in the site database). Claude registered 2 OAuth clients per connect attempt (4 for two attempts, 1 token issued), so the shim's 20 registrations per hour per IP allows roughly 10 attempts. The same flow is scripted in `dev/oauth_smoke.py` through the tunnel: 401 → discovery → registration → authorize (PKCE) → token → refresh → `tools/list` → `submit_plan` → refused write → `approve_plan` → write → replay refused → revoked/garbage token gets 401 with `WWW-Authenticate` | **PENDING** — same |
| 5 | `onnxruntime`/`tokenizers` install alongside Frappe's pins | **PASS** — onnxruntime 1.31.0, tokenizers 0.23.3, numpy 2.5.4 from wheels on Python 3.12; `pip check` clean, only additions | **PASS** — same versions from wheels on Python 3.14; `pip check` clean |

**Verdict:** GO if rows 1, 2, 4 and 5 pass on both versions (row 3 may be DEFERRED). Rows 1, 2 and 5 pass on both. Row 4 passed on v15 (owner's Claude test) and is still waiting for the same test on v16. Provisional verdict: **GO, pending row 4 on v16.**

## Findings worth keeping

- **v16 core registers clients with scope `all` only**, so the smoke script requests the scope the server registered instead of a hard-coded `all openid`. Both versions also work when the `scope` parameter is omitted entirely (which is what a client does when the server advertises no `scopes_supported`).
- **v16 core refuses `http://` redirect URIs on an https site** ("redirect_uris must be https"). Claude's callback is https, so this is expected.
- **v15 core never validates client secrets** and enforces PKCE only when the client sends a `code_challenge`. The v15 shim therefore advertises `none` and `client_secret_post`; document PKCE-only public clients and a dedicated low-privilege user in the README.
- **A bad, expired or revoked bearer token on v15 got a 401 without `WWW-Authenticate`** (Frappe rejects it before the endpoint runs); fixed by an `after_request` hook and covered by the smoke test and WSGI tests.
- **`bench serve` needs `--proxy` behind a tunnel** for v16 core to build `https://` metadata URLs. Set `host_name` (no trailing slash) and `restart_supervisor_on_update` on v15, otherwise `get_url()` appends `:8000` in the dev bench.
