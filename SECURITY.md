# Security

Nurby processes camera footage and recognizes people, so a careful
deployment matters. This document covers hardening for self-hosters and
how to report a vulnerability.

## Hardening checklist

Before exposing Nurby beyond your local machine.

- **Set `JWT_SECRET`** to a strong, persistent value. Without it, Nurby
  generates a random secret per process and everyone is logged out on
  every restart. Generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
- **Change `POSTGRES_PASSWORD`** from the `nurby_dev` template default.
- **Do not expose Postgres, Redis, or the MediaMTX API** to the public
  internet. The compose file binds them to localhost. Keep it that way.
- **Put the API and frontend behind HTTPS** via a reverse proxy. Set
  `PUBLIC_BASE_URL` to the public address so alert links are correct.
- **Restrict CORS** with `CORS_ORIGINS` to the origins you actually use.
- **Treat API keys and webhook secrets as credentials.** the API key
  plaintext is shown once. Signed webhooks use an HMAC secret you share
  with the receiver.
- **Keep physical device receivers on your LAN.** the ESP32 / Raspberry
  Pi alert scripts listen on plain HTTP and verify a shared HMAC secret.
  do not port-forward them.
- **Review who has accounts.** invite keys grant access with a role and
  per-camera scope. Revoke unused keys and accounts.

## What ships safe by default

- No secret values are committed. `.env` is gitignored and was never in
  history.
- Passwords are stored as bcrypt hashes. API keys are stored as sha256
  hashes, never plaintext.
- The JWT secret default is a placeholder that triggers a loud warning
  and a generated random secret rather than a shared signing key.
- CORS is an explicit allowlist, not a wildcard with credentials.

## Reporting a vulnerability

Please do not open a public issue for security problems. Instead, report
privately through GitHub Security Advisories on this repository, or by
contacting the maintainer directly. Include steps to reproduce and the
impact. We aim to acknowledge reports promptly and will credit reporters
who want it once a fix is released.
