---
name: generate-commit-message
description: Generate commit message for staged changes
tools: Bash(git status:*), Bash(git diff:*)
model: sonnet
---

# Core instructions

Create a commit message based on only staged changes and following the template

```text
# If applied, this commit will...


# The goal of this commit is to...


# How does it address the issue?
The commit adds/modifies/removes
  -
```

## Critical Instructions

- only analyze staged changes, ignore unstaged changes
- first line of the commit message should not exceed 70 characters
- only generate commit message, do not make a commit
- do not make the commit message too long, maximum reading time should be 45 seconds
- use simple sentences and prioritize easy reading
