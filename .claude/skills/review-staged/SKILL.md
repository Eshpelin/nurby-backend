---
name: review-staged
description: >
  Review staged changes against best practices.
  Delegates to the staged-reviewer agent which enforces read-only tool access.
  Only invoke when the user explicitly calls /review-staged.
---

# Staged Changes Review Skill

Delegate staged changes review to the `staged-reviewer` agent.

## Usage

```
/review-staged
```

No arguments needed — reviews whatever is currently staged in git.

## Execution

1. **Delegate to the `staged-reviewer` agent** with this exact prompt:

   ```
   Review the currently staged changes.
   ```

2. **Return the agent's review** to the user verbatim.

## Notes

- All review logic and tool restrictions are handled by the `staged-reviewer` agent — do not duplicate them here.
- The agent is strictly read-only: it will never edit files, create commits, or alter git state in any way.
- This skill does not trigger automatically. It only runs when the user explicitly invokes `/review-staged`.
