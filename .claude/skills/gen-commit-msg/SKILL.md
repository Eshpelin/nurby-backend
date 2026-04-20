---
name: gen-commit-msg
description: >
  Generate a commit message for staged changes. Delegates to the `generate-commit-message`
  agent. Only invoke when the user explicitly calls /gen-commit-msg.
---

# Commit Message Generation Skill

Delegate commit message generation to the `generate-commit-message` agent.

## Usage

```
/gen-commit-msg
```

## Notes

- All logic is handled by the `generate-commit-message` agent — do not duplicate it here.
- The agent is strictly read-only: it will never make a commit or modify any files.
- This skill does not trigger automatically. It only runs when the user explicitly invokes `/gen-commit-msg`.
