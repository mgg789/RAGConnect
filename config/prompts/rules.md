## Prompt routing rules

- If `memory-label` is provided by project instructions (`AGENTS.md` or `CLAUDE.md`), pass it as `project_label` for memory operations with this project.
- Do not use project labels for personal notes or private user memory.
- If no local memory is configured, work in remote-only mode and always pass `project_label`.
- Never silently ignore memory write failures.
