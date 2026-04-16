## Memory routing rules

- If project instructions define `memory-label`, always use that value as `project_label` for project work.
- If no `project_label` is provided, local memory is the default personal memory.
- Do not mix personal notes into project memory unless the note is actually shared project knowledge.
- If project memory is unavailable and local fallback is not allowed, report the failure explicitly.
- Never silently skip a memory write.

## Heuristics

Use project memory when the fact should be shared with the project or team:
- architecture decisions
- constraints
- implementation details that matter later
- bug root causes
- milestones and outcomes
- stable project-specific preferences

Use local memory when the fact is personal or cross-project:
- personal preferences
- temporary reminders
- observations that should not be shared into one project label

## Expected habit

Think in this order:
1. Is this a project fact or a personal fact?
2. Should I search memory before answering?
3. Should I write this back so it is not lost later?
4. If this is project work, am I using the right `project_label`?
