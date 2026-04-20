---
name: create-pr
description: >
  Create a draft GitHub pull request for the current branch. Delegates to the
  `pr-creator` agent. Only invoke when the user explicitly calls /create-pr.
---

# Pull Request Creation Skill

Delegate pull request creation to the `pr-creator` agent.

## Usage

```
/create-pr
```

No arguments needed — the agent analyzes commits on the current branch against the default base branch.

## Execution

1. **Delegate to the `pr-creator` agent** with this exact prompt:

   ```
   Analyze the commits on the current branch and create a draft pull request.
   ```

2. **Return the agent's output** (including the PR URL) to the user verbatim.

## Notes

- All PR authoring logic is handled by the `pr-creator` agent — do not duplicate it here.
- The agent may push the current branch to the remote and create a draft PR via the `gh` CLI. It will not edit or write files.
- This skill does not trigger automatically. It only runs when the user explicitly invokes `/create-pr`.
