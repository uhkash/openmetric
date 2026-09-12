# Security

OpenMetric holds every API key you give it. This document describes what it does to
protect them, what it deliberately does not do, and how to report a problem.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Use GitHub's private
reporting: **Security → Report a vulnerability** on this repository.

Include what you did, what happened, and what you expected. A proof of concept helps.
Expect an acknowledgement within a few days.

Never include a real API key in a report. If you believe a key of yours was exposed,
rotate it at the provider first — that is always the first move, before anything else.

## Threat model

OpenMetric is designed to run **on your own machine or your own server**, reachable by
you. It is not multi-tenant, and it is not hardened for exposure to the open internet.

What it protects against:

- **A leaked database file.** Credentials are encrypted; virtual keys are hashed. Someone
  who copies `openmetric.db` gets neither your provider keys nor a working gateway token.
- **Accidental exposure through the app.** No API response, dashboard view, CLI output,
  log line or CSV export contains a stored key — only a hint and a fingerprint.
- **Accidental exposure through git.** `.env`, `*.db`, `*.key` and `*.pem` are gitignored;
  CI runs gitleaks; tests fail if a secret-bearing file is ever staged.
- **Accidental exposure through support channels.** Errors and logs are scrubbed of common
  vendor key formats, so a pasted stack trace does not carry a credential with it.

What it does **not** protect against, and you should know this:

- **An attacker who already has your machine.** `OPENMETRIC_SECRET_KEY` sits in `.env` next
  to the database. Anyone who can read both can decrypt your keys. This is the standard
  trade-off for a local tool; if you need more, put the key in a secrets manager and
  inject it at runtime.
- **An unauthenticated open port.** If you bind beyond `127.0.0.1` without setting
  `OPENMETRIC_ADMIN_TOKEN`, anyone who can reach the port can spend your money. OpenMetric
  warns you at startup; please listen to it.
- **Malicious upstream providers.** Requests are proxied largely as received.

## How credentials are handled

| Stage | Behaviour |
|---|---|
| Entry | Hidden prompt or environment variable. There is no `--api-key` CLI flag |
| At rest | Fernet (AES-128-CBC + HMAC-SHA256), key from `OPENMETRIC_SECRET_KEY` |
| In the database | Ciphertext, plus a `last4` hint and a truncated SHA-256 fingerprint |
| In use | Decrypted in memory only to sign one upstream request |
| In responses | Never. Not in the API, the dashboard, the CLI, or a CSV export |
| In logs | Never. All log and error text passes through redaction first |

The fingerprint is a one-way hash. It exists so you can tell whether two stored keys are
the same key — useful for spotting one key copied across several projects — without the
system ever having to show you a key to compare.

## Virtual keys

Applications authenticate to OpenMetric with a virtual key (`om_live_...`), not a provider
key. These are stored as SHA-256 hashes and shown exactly once, at creation.

Revoke one with `openmetric vkey revoke <name>`. The provider key behind it is untouched,
so revoking a compromised application token does not require rotating anything upstream.

## Request and response bodies

Prompt and response text is **not stored by default**. `OPENMETRIC_LOG_REQUEST_BODIES` and
`OPENMETRIC_LOG_RESPONSE_BODIES` turn it on for debugging; even then, bodies are truncated
and passed through redaction before being written. Turning these on means your prompts sit
in a local database — do it deliberately, and prune with `openmetric prune --days 7`.

## Rotating your encryption key

There is no automatic re-encryption. To rotate:

1. Note which credentials you have (`openmetric key list`).
2. Delete them (`openmetric key delete` / the API).
3. Replace `OPENMETRIC_SECRET_KEY`.
4. Re-add the credentials.

Your usage history is unaffected — events hold no key material.

## Supported versions

This is pre-1.0; fixes land on `main`. Please report against the latest commit.
