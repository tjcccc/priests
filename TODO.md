# TODO

## spmem Maintenance

spmem (Structured Personal Memory) is complete for the current `priests` showcase chat app. Do not add more memory features here unless they fix regressions. Deeper memory-system work should wait for the future personal agent app, where real agent workflows can drive the design.

- Fix memory regressions found during normal chat use.
- Keep deterministic memory tests and the professional live eval passing before memory releases.

## Deferred Future Agent spmem Work

These are intentionally not current `priests` TODO items: token-aware budgeting, memory scopes/topics, tool-context selection, privacy classes, optional encryption, used-memory tracing, storage abstraction, semantic retrieval, larger adversarial evals, and open-source extraction. Revisit them when the agent app starts.

## Showcase Chat App Polish

- Add a model health/status panel in the UI using the provider status API.
- Wire local model storage listing/cleanup into the UI.
- Improve first-run onboarding in the UI.
- Wire provider/model validation into config saves in the UI.
- Add a cleaner empty state for new users.
- Wire profile/config import-export into the UI if it proves useful; CLI support already exists.
- Improve visual polish around errors and long-running model startup.
