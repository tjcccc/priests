# Changelog

All notable changes to `priests` are documented here.

## [0.18.0] — 2026-05-08

### Added
- Profile-scoped model overrides via optional `provider` and `model` fields in `profile.toml`
- `priests model default --profile <profile>` to set or clear a profile model override from the CLI
- Profile Configuration web UI dropdown for selecting a profile model from added models
- Repo-level `AGENTS.md` guidance and local development start commands in README

### Fixed
- Chat composer provider/model selectors now reflect the selected profile's scoped model before falling back to the global default
- Chat message bubbles stay content-sized and align consistently with the composer column

---

## [0.17.0] — 2026-04-25

### Added
- Config UI: fixed sidebar nav with smooth-scroll links to all 8 sections
- `ModelSelect` component: three modes — dynamic fetch from provider, free text, or curated dropdown with custom escape hatch; remounts on provider change
- Providers grouped by type (Local → API → OAuth) in dropdowns
- Dynamic model fetching via `GET /v1/providers/{name}/models` — proxies Ollama, llama.cpp, LM Studio; returns `known_models` for API providers
- GitHub Copilot and ChatGPT OAuth providers — registry, engine factory, config model, config route, UI; `ProviderCard` shows auth guidance for OAuth providers
- `provider_type` field on `ProviderRegistryItem` for frontend grouping
- Profile rename/delete: hover icons in sidebar, inline rename with Enter/Escape handling, `window.confirm()` delete guard
- Backend: `POST /profiles/{name}/rename`, `DELETE /profiles/{name}`; both protected against the `default` profile; regex-validated names
- Proxy section wired to `PATCH /v1/config { "proxy.url": "..." }`

### Fixed
- Silent no-op when `use_proxy` was enabled but `[proxy]` block was absent from `priests.toml`

---

## [0.16.0] — 2026-04-25

### Added
- New providers: llama.cpp (`localhost:8080`), LM Studio (`localhost:1234/v1`), Mistral AI, Together AI, Perplexity, Cohere
- `GET /v1/config` — returns full `AppConfig` with API key values masked
- `PATCH /v1/config` — applies dotted-key updates, hot-reloads adapters, returns `{ needs_restart }` for service host/port changes
- Config page sections: Defaults, Profile Configuration, Model Configuration, Providers, Memory, Web Search, Service, Paths, Proxy; per-section Save buttons; show/hide toggle for API key fields
- `build_adapters(config)` extracted from `build_engine()` for hot-reload without touching the session store
- Memory system (`StreamingStripper`, file helpers, `clean_last_turn`, `pop_last_exchange`) extracted to `priest-core` v2.2.0; `priests/memory/extractor.py` is now a thin re-export shim

---

## [0.15.0] — 2026-04-20

### Added
- Session context menu: Pin, Rename, Delete with confirmation dialog
- Pinned sessions sorted to top with indicator; `PUT /v1/ui/sessions/{id}/pin` toggle
- React Router v6; session URLs (`/ui/session/:id`) survive page refresh
- `turn_meta` table — stores model and elapsed ms per turn; assistant footer persists across refresh
- `DELETE /v1/sessions/{id}` — removes session, turns, uploads, turn_meta, ui_meta, and upload files from disk
- SPA catch-all routes replace `StaticFiles(html=True)` — deep-path refresh no longer 404s

### Fixed
- Image accumulation bug: `upload_uuids` now contains only the current turn's images, not all prior turns

---

## [0.14.0] — 2026-04-19

### Added
- React 18 + TypeScript + Vite + Tailwind CSS v4 web UI at `/ui`
- Frosted-glass sidebar, profile/session tree, streaming chat area, Thinking toggle
- Sessions grouped by profile; per-profile New Session button
- Session URLs auto-loaded on refresh; built `dist/` served by FastAPI on the same port as the API
- React Router v6 routes: `/ui` (home), `/ui/session/:sessionId` (chat), `/ui/config` (stub)

---

## [0.13.0] — 2026-04-19

### Added
- `priests service start -d` — background daemon mode; PID → `~/.priests/service.pid`, logs → `~/.priests/service.log`
- `priests service stop` — SIGTERM daemon, clears PID file
- `priests service restart` — stop + re-start daemon
- `priests service logs [-f] [-n N]` — tail daemon log; `-f` follows live output
- `-h`/`-p` short flags on `start`, `restart`, `status`

### Fixed
- Streaming memory block: consolidation failure no longer silently skips append/trim

---

## [0.12.0] — 2026-04-19

### Added
- `RunRequest` accepts `images: list[ImageIn]` (URL or base64); forwarded to all `/v1/run` and `/v1/chat` routes
- SSE streaming routes: `/v1/run/stream` and `/v1/chat/stream`; each chunk is `data: {"delta": "..."}`, terminal event is `data: [DONE]`
- `StreamingStripper` handles memory-block filtering mid-stream; memory consolidation runs post-stream
- Server-side image uploads: `POST /v1/uploads`, `GET /v1/uploads/{uuid}`, `GET /v1/sessions/{id}/uploads`; files saved to `~/.priests/uploads/` with Pillow compression
- Drag-and-drop onto input card; images uploaded immediately on attach; Send disabled while upload is in flight
- 10 `TestClient`-based service tests

---

## [0.11.0] — 2026-04-19

### Added
- `priests model rm <provider/model>` — removes a model from the list; clears and re-prompts for default if the removed model was active

---

## [0.10.0] — 2026-04-19

### Added
- Agentic file reading: model emits `<read_file>/path</read_file>`; CLI reads up to 100 KB, injects as `user_context`, pops probe exchange, re-prompts
- `StreamingStripper` extended with `<read_file>` block capture

### Fixed
- Broken `ddgs` import (`duckduckgo_search` → `ddgs`); promoted to core dependency

---

## [0.9.0] — 2026-04-19

### Added
- Agentic auto-search loop: model emits `<search_query>QUERY</search_query>`; CLI intercepts, runs DuckDuckGo search, pops probe exchange, re-prompts with results — transparent to the user
- Manual `/search <query>` slash command still available for explicit searches

---

## [0.8.0] — 2026-04-19

### Changed
- Adapted to `priest-core` v2.0.0 API: `system_context` → `context`, `extra_context` → `user_context`; backward-compat shim in `RunRequest`

### Fixed
- Hallucinated search: strengthened web search context hint to state the model has no built-in search tool

---

## [0.7.0] — 2026-04-12

### Added
- `WebSearchConfig` (`enabled`, `max_results`) in `AppConfig`; `/search <query>` slash command in chat
- `/remember` and `/remember!` slash commands for direct memory writes
- `MemoryConfig.flat_line_cap` — soft on-disk line cap for `user.md`/`notes.md` enforced via consolidation prompt hint

### Changed
- Switched `priest-core` dep from local editable path to `>=1.0.0` on PyPI

### Fixed
- Memory injection: `## Loaded Memories` now injected on all non-consolidation turns (was only during consolidation)
- `/new` consolidation state not reset between sessions

---

## [0.6.0] — 2026-04-07

### Added
- `deduplicate_file(path)` — strips exact duplicate lines from `user.md`/`notes.md` at session start
- `MemoryConfig.context_limit` — caps combined memory size injected per turn; drops oldest `auto_short` sections first
- 31 correctness tests + 13 performance benchmarks for memory system

### Fixed
- `trim_memories` bug: last dated section was silently dropped when file exceeded `size_limit`
- Dedup now runs before `needs_consolidation` so a dedup write does not falsely trigger consolidation

---

## [0.5.0] — 2026-04-05

### Added
- Rolling `auto_short.md` with dated `## YYYY-MM-DD` sections replaces scattered `auto_YYYYMMDD.md` files
- `user.md` (permanent facts) and `notes.md` (role constraints) as separate long-term memory files
- `memory.size_limit` (character count) replaces `memory.limit` (file count); `trim_memories` drops oldest dated sections
- Per-turn `<memory_append>` block: model appends to all three files before streaming response
- First-turn `<memory_consolidation>` block with sentinel file to prevent re-triggering
- `StreamingStripper` rewritten as explicit state machine; handles both block types cleanly
- `apply_consolidation` always writes when key is present; normalizes `auto_short` to dated-section format

### Changed
- All CLI command groups renamed to singular (`providers` → `provider`)
- `--think`/`--memories` changed from bool flags to value options with `_parse_bool()`
- Bare `priests "text"` now errors (`no_args_is_help=True`)

---

## [0.4.5] — 2026-04-04

### Fixed
- Spurious `[` character appearing before `user >` input prompt — caused by mixing
  Rich `console.print()` and raw `sys.stdout.write()` in the streaming loop;
  the ESC byte from Rich's ANSI bold code was consumed by prompt_toolkit, leaving
  the bare `[` visible; replaced all in-loop Rich calls with `sys.stdout.write()`
  using raw ANSI codes

---

## [0.4.4] — 2026-04-04

### Fixed
- `--version` reported `v0.1.0` instead of the current version — `__init__.py` was
  not updated alongside `pyproject.toml`
- Streaming chat printed `{profile} >` immediately on Enter, before the model began
  responding — made it look like an input prompt; now deferred until the first token
  arrives
- Streamed response had words run together with no spaces — `strip_memory_tags()`
  called `.strip()` per-chunk, eating leading/trailing whitespace between chunks
- Memory tags leaked into terminal output when split across stream chunks —
  `StreamingStripper` now buffers from the first `<memory` occurrence and only
  flushes safe text, emitting the remainder on stream end
- Profile memories not recalled by the model — facts were injected as unlabeled raw
  text; now collected under a `## Loaded Memories` heading in the system prompt
  (fix in `priest-core` `context_builder.py`)

---

## [0.4.0] — 2026-04-04

### Added
- Streaming output in CLI — `priests run` and interactive chat now print tokens
  as they arrive using `engine.stream()`; time-to-first-token ~215ms vs ~3s wait
  for buffered responses (14× perceived speedup on local models)

---

## [0.3.0] — 2026-04-04

### Added
- Provider registry (`priests/registry.py`) — 12 providers with curated model
  lists: Ollama, OpenAI, Anthropic, Gemini, Bailian, Alibaba Cloud, MiniMax,
  DeepSeek, Kimi, Groq, OpenRouter, Custom
- `priests model list` — list saved provider/model pairs, marks current default
- `priests model default` — interactive arrow-key selection of default model
- `priests model add` — guided flow: select provider, enter API key, pick model
- `priests providers` — table of all providers with configured/unconfigured status
- `priests providers models <name>` — show curated model list or fetch Ollama
  models dynamically
- Proxy support — `[proxy] url` in `priests.toml`; `use_proxy = true/false` per
  provider; engine resolves and passes proxy URL to adapter constructors
- DeepSeek provider (`api.deepseek.com`) — `deepseek-chat`, `deepseek-reasoner`
- Kimi / Moonshot provider (`api.moonshot.cn`) — K2.5, K2-thinking, moonshot-v1
- `[models]` section in `priests.toml` — stores `provider/model` pairs

### Fixed
- Gemini base URL corrected to `/v1beta/openai/` (was `/openai/v1` → 404)
- MiniMax base URL corrected to `api.minimax.io/v1` (was `api.minimax.chat/v1` → 401)
- `think` parameter now only injected for Qwen/Ollama providers; other providers
  (Gemini, OpenAI, etc.) receive empty `provider_options` to avoid HTTP 400

### Changed
- Provider labels updated: `(domestic)` → `(China mainland)`
- Model lists refreshed: gpt-4.1 family, qwen3.5, kimi-k2.5, MiniMax M2.x,
  Claude 4.x only, Gemini 2.5 series, current Groq production models

---

## [0.2.0] — 2026-04-03

### Added
- Autonomous memory system — model emits `<memory>` tags; tags are extracted
  after each turn and written to the profile's `memories/` directory
- Three memory categories: `user.md` (stable facts), `notes.md`
  (role-important), `auto_YYYYMMDD.md` (daily observations)
- `~/.priests/PRIESTS.md` — memory guide bootstrapped automatically on first
  run; teaches the model tag syntax and when to save
- `priests run --no-memories` — disable memory for a single session
- `memories = false` in `profile.toml` — disable memory permanently for a profile
- `memories_limit` in `profile.toml` — override global memory file limit
- `[memory] limit` in `priests.toml` — global cap on daily auto memory files
- Per-profile `profile.toml` with `memories`, `memories_limit` settings
- `/new` slash command in interactive chat — starts a new session
- Arrow key navigation and Ctrl+J newline in interactive chat (prompt_toolkit)

### Fixed
- Memory tag deduplication — known facts are not re-saved across sessions
- Placeholder filter — prevents hallucinated `[Unknown]` / `[Name]` values
  from being written to memory files
- Session tag cleanup — memory tags stripped from last assistant turn in
  SQLite so they don't leak into future conversation context

---

## [0.1.0] — 2026-03-31

### Added
- `priests run` — interactive chat (prompt_toolkit) and single-prompt CLI
- `priests init` — guided setup: provider selection, API key entry, model pick,
  writes `~/.priests/priests.toml`
- `priests config show / set` — read and update config values
- `priests profile list / init` — manage behavior profiles
- `priests service start / status` — FastAPI HTTP service on `127.0.0.1:8777`
- HTTP API: `POST /v1/run`, `POST /v1/chat`, `GET /v1/sessions`,
  `GET /v1/sessions/{id}`, `GET /health`
- `/think on` / `/think off` / `/exit` / `/help` slash commands in chat
- `~/.priests/priests.toml` config with `[default]`, `[paths]`, `[service]`,
  `[providers.*]`, env var overrides via `PRIESTS_` prefix
