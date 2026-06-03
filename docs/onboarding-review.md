# Onboarding self-review

A pass over the first-run flow looking for caveats, edge cases, and reuse
opportunities. Items marked DONE were fixed in the same pass.

## Correctness / lockout bugs

1. **Provisional owner could log themselves out forever.** DONE.
   A provisional account has random, unusable credentials. The navbar
   showed a plain Logout. clicking it cleared the token, and the next
   visit saw `users > 0` so `bootstrap` would not re-fire, leaving the
   user at `/login` with no password that works. Fix. hide Logout while
   provisional and surface only the Secure-account path.

2. **Concurrent first-run requests could create two owners.** DONE.
   `bootstrap` did check-count-then-insert with no lock. Two tabs or a
   double-fired effect could both pass the `count == 0` check. Fix. take a
   transaction-scoped Postgres advisory lock so bootstraps serialize. the
   second sees `count > 0` and returns 409.

3. **`/setup` was a dead end on an existing install.** DONE.
   Visiting `/setup` after setup completed posted `/auth/setup`, got 409,
   and stranded the user on a doomed form. Fix. the page now checks
   `needs-setup` on mount and redirects home when setup is already done.

4. **Demo camera duplicated on repeat.** DONE.
   `POST /cameras/demo` always inserted. Running magic twice, or clicking
   the dashboard demo button again, produced multiple "Demo Camera" rows.
   Fix. return the existing demo camera (matched by stream URL) instead.

5. **Magic could run twice (React strict mode / remount).** DONE.
   The provisioning effect had no idempotency guard, so a remount could
   fire two demo-camera POSTs and two deploys. Fix. a ref guard runs the
   sequence once per mount lifetime.

## Robustness already handled (verified, no change)

- Magic VLM step is best-effort. no reachable Ollama is marked "skipped"
  and never blocks the dashboard, since detection, faces and rules need no
  VLM.
- Magic bails to the manual flow if the demo camera (the one hard
  requirement) cannot be created, rather than landing on an empty page.
- `bootstrap` returns 409 when an account already exists, and `auth.tsx`
  falls back to `/login` cleanly.
- The HTTP-pull deploy path registers the provider at the reachable URL,
  so the bundled Ollama service works without a host binary.

## Polish

6. **Escape to dismiss the wizard.** DONE. except during magic, where
   work is in flight.
7. **Dead `submitting` prop** on `ProviderStep`. DONE. removed.

## Known tradeoffs (intentional, documented)

- First visitor on the network becomes the provisional owner. This is the
  cost of "no signup wall." mitigated by the loud Secure-account prompt.
  An operator who wants a hard wall can still use `/setup` first.
- Magic only pulls a model when an Ollama is already reachable. On a
  stock Docker `up` with the `local-ai` profile not started, magic adds
  the camera and honestly skips the model.
- The deploy progress creep (42 to 88 percent) is cosmetic. the pull is a
  single long call with no server-side increments. the checkmarks are
  real.
