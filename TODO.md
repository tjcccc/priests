# TODO

## Structured Priority Memory Follow-ups

- Add a memory viewer/editor for active, superseded, and per-profile entries.
- Show memory sources or "used memory" indicators in chat responses when memory affects an answer.
- Add privacy classes and never-save rules for sensitive memory.
- Add optional encryption or protected storage for local memory files.
- Add observability logs for saved, rejected, fallback-extracted, updated, and forgotten memories.
- Add token-aware or model-context-aware budgeting; current character budgets are intentionally lightweight.
- Add lightweight memory scopes/topics only if recall quality needs them, e.g. `global`, `chat`, `coding`, `personal_admin`.
- Add tool selection before prompt construction so cheap chat injects no tools and task prompts inject only relevant tools.
- Add a storage abstraction so SPM can move from JSONL to SQLite or hybrid vector storage later.
- Add optional semantic retrieval for large memory sets and long-running agent projects.
- Expand professional live memory evals with more multilingual prompts, adversarial save/forget cases, and longer memory stress tests.

## Showcase Chat App Polish

- Add a model health/status panel showing which local providers are reachable.
- Add one-click local model storage listing and cleanup.
- Improve first-run onboarding in the UI.
- Add live provider/model validation when saving config.
- Add a cleaner empty state for new users.
- Add optional import/export for profiles and config.
- Improve visual polish around errors and long-running model startup.
