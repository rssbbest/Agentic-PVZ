---
inclusion: always
---

# Codebase Editing Rules

Always follow these rules when changing code in this project:

- Don't rewrite files fully. Make the minimal change that does the job.
- Follow the existing naming scheme and code formatting of the file being edited.
- Mark every newly added block of code with a comment indicating it is new (e.g. `// NEW: <what it does>`).
- Keep comments concise and short.
- Use `[SerializeField]` over `public` for fields that only need to show in the Inspector. Make a field `public` only when another script must read or set it, and prefer a getter (like `GetCurrentHealth()`) when it only needs reading.
- Use traditional, readable C# syntax like the existing codebase. Avoid compact or clever syntax just to shorten lines:
  - No ternaries (`a ? b : c`); use `if / else` blocks.
  - No `var`; declare explicit types.
  - No inline `out var` declarations; declare the variable first.
  - No lambdas or LINQ where a plain loop works.
  - No chained assignments (`a = b = c`) or single-line `if` bodies; use braces on their own lines.
