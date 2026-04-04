# Changelog

All notable changes to `priests` are documented here.

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
