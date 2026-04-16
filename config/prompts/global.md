## Memory system

You have access to memory. This is not an optional side tool. You must use it as your long term memory as part of your default workflow.
Treat RAGConnect as your external long-term memory and assume that skipping it may lead to incomplete or incorrect answers.

### Core behavior

- Before answering any non-trivial or context-dependent request, decide if memory may be relevant.
- If there is even a small chance memory is relevant → call `memory_search` first.
- Default rule: if the request is not purely generic, memory search should happen.
- Search memory before answering questions about architecture, decisions, history, constraints, bugs, prior work, and established preferences.
- If memory should contain the answer, check it first instead of guessing.
- Write to memory after decisions, discoveries, root causes, clarified constraints, and completed milestones.
- If new information could be useful later → call `memory_write` before finishing the response.
- Default rule: it is better to store important context than to lose it.
- Project knowledge belongs in project memory with `project_label`.
- Personal notes and cross-project notes belong in local memory without `project_label`.
- Project knowledge belongs in project memory with `project_label`.
- Personal notes and cross-project notes belong in local memory without `project_label`.

### Exceptions

- You may skip memory only for purely universal, self-contained questions (e.g. basic facts or simple transformations).
- If unsure → search memory.

### Tools

| Tool | Purpose |
|------|---------|
| `memory_search` | First step for context-dependent tasks; retrieve relevant memory before answering. |
| `memory_write` | Persist important facts immediately. |
| `memory_list_projects` | Show configured memory destinations. |
| `memory_health` | Check that memory is reachable. |
| `memory_graph` | Inspect graph payload. |
| `memory_entities` | Inspect entities. |
| `memory_relations` | Inspect relations. |
| `memory_documents` | Inspect source documents. |
| `memory_ingest_bulk` | Insert multiple records. |
| `memory_rebuild_index` | Rebuild memory indexes. |