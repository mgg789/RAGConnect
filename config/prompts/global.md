## Memory system

You have access to memory. This is not an optional side tool. You must use it as your long term memory always.
Treat RAGConnect as your external long-term memory and use it as part of your default workflow.

### Core behavior

- Search memory before answering questions about architecture, decisions, history, constraints, bugs, prior work, and established preferences.
- Write to memory after decisions, discoveries, root causes, clarified constraints, and completed milestones.
- Project knowledge belongs in project memory with `project_label`.
- Personal notes and cross-project notes belong in local memory without `project_label`.
- If memory should contain the answer, check it first instead of guessing.

### Tools

| Tool | Purpose |
|------|---------|
| `memory_search` | Read from memory before answering. |
| `memory_write` | Persist important facts immediately. |
| `memory_list_projects` | Show configured memory destinations. |
| `memory_health` | Check that memory is reachable. |
| `memory_graph` | Inspect graph payload. |
| `memory_entities` | Inspect entities. |
| `memory_relations` | Inspect relations. |
| `memory_documents` | Inspect source documents. |
| `memory_ingest_bulk` | Insert multiple records. |
| `memory_rebuild_index` | Rebuild memory indexes. |
