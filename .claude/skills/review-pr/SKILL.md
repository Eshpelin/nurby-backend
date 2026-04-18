---
name: review-pr
description: >
  Review a GitHub pull request. Delegates to the pr-reviewer agent which
  enforces read-only tool access. Only invoke when the user explicitly calls /review-pr.
---

# PR Review Skill

Delegate PR review to the `pr-reviewer` agent.

## Usage

```
/review-pr <PR URL>
```

**Arguments:**

- `<PR URL>`: A full GitHub PR URL (e.g. `https://github.com/<owner>/<repo>/pull/12345`)

## Execution

1. **Extract the PR number and repo** from the URL:
   - `https://github.com/<owner>/<repo>/pull/12345` → repo: `<owner>/<repo>`, number: `12345`
   - If no valid PR URL can be parsed, tell the user and stop.

2. **Delegate to the `pr-reviewer` agent** with this exact prompt:

   ```
   Review PR #<N> from the <owner>/<repo> repository.
   ```

3. **Return the agent's review** to the user verbatim.

## Notes

- All review logic and tool restrictions are handled by the `pr-reviewer` agent — do not duplicate them here.
- The agent is strictly read-only: it will never approve, reject, comment on, or mutate the PR in any way.
- This skill does not trigger automatically. It only runs when the user explicitly invokes `/review-pr`.
