# DEVLOG

## Future milestones

### Autonomous memory system (v0.2+)

Profile memories (`memories/`) are currently **read-only** — loaded into the system prompt at startup but never written back. There is no mechanism to extract facts from a conversation and persist them as memory files.

Planned work:
- After each session (or on demand via `/remember`), extract key facts and write them to the profile's `memories/` directory as `.md` files
- A `GUIDE.md` (or `AGENT.md`) in the profile directory to describe role-specific memory behavior: what to remember, what to forget, how to format entries
- Memory selection/ranking (currently all memories are loaded in filename order — `priest` core deferred this from Milestone 1)
- `/new` slash command in chat to start a fresh session while keeping the profile loaded

### Other deferred items
- `service stop` / `service logs` with background daemon mode (PID file)
- `priests models add` — add/configure additional providers after init
- `/new` session command in interactive chat
- Multi-model dispatch, cron task pool, agent orchestration
- OpenAI provider (tracked in `priest` Milestone 2)
- `priest` `SqliteSessionStore.list()` method (priests uses direct aiosqlite query as workaround)

---

## 2026-04-01 — Milestone 1 complete

Initial implementation of `priests` v0.1.0. All planned CLI commands and HTTP service routes are in place and verified against Ollama + qwen3.5:9b.

**Package setup:**
- `pyproject.toml` with `uv`, hatchling, `priest` as local path dep
- Added `tool.hatch.metadata.allow-direct-references = true` (required for path deps)
- Entry point: `priests = "priests.cli.main:app"`

**Config system (`priests/config/`):**
- `AppConfig` with `DefaultsConfig`, `PathsConfig`, `ServiceConfig`, `ProvidersConfig` (all Pydantic)
- Config file: `~/.config/priests/priests.toml` (XDG) or `~/.priests/priests.toml`
- Env var overrides: `PRIESTS_` prefix, `__` for nesting (e.g. `PRIESTS_DEFAULT__MODEL`)
- `set_config_value()` mutates a dotted key and writes back via `tomli-w`
- `None` values stripped before TOML serialization (TOML has no null type)

**Engine factory (`priests/engine_factory.py`):**
- Single function: `build_engine(config) -> (PriestEngine, SqliteSessionStore)`
- Store returned un-initialized — caller owns lifecycle (`async with store:` for CLI, lifespan for FastAPI)

**CLI (`priests/cli/`):**
- `priests run [PROMPT]` — single prompt or interactive chat (tty detection); `--no-think` default
- `priests profile list / init NAME` — scans profiles dir, scaffolds PROFILE.md + RULES.md + memories/
- `priests config show / set KEY VALUE` — rich TOML display, dotted key mutation
- `priests service start / status / stop / logs` — foreground uvicorn; status hits `/health`

**HTTP service (`priests/service/`):**
- FastAPI app via `create_app(config)` with lifespan-managed engine + store
- `GET /health`, `POST /v1/run`, `POST /v1/chat`, `GET /v1/sessions`, `GET /v1/sessions/{id}`
- `/v1/chat` auto-generates session ID if none provided
- Session listing uses direct `aiosqlite` query (SqliteSessionStore has no `list()` — raise PR to priest for v0.2)
- `SessionSummary` / `SessionDetail` Pydantic models bridge priest's `Session` dataclass to JSON

**Verified against:** Ollama + qwen3.5:9b locally. Session continuity confirmed via `/v1/chat`.

### Out of scope for v0.1.0 (deferred)

- `service stop` / `service logs` (foreground-only; stubs added)
- Multi-model dispatch, cron task pool, agent orchestration (v1+)
- OpenAI provider (tracked in `priest` Milestone 2)
- `priest` `SqliteSessionStore.list()` method (direct aiosqlite query used as workaround)
- Background service mode with PID file
