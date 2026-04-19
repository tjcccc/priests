# DEVLOG

## 2026-04-19 — v0.13.0 — service command daemon mode + test hardening

- `priests service` / `priests service start` run foreground by default (live terminal output)
- `priests service start -d` spawns a background daemon; PID → `~/.priests/service.pid`, logs → `~/.priests/service.log`
- `priests service stop` — SIGTERM daemon, clears PID file
- `priests service restart` — stop + re-start daemon
- `priests service logs [-f] [-n N]` — tail daemon log; `-f` follows live output
- `priests service status` — pings `/health`; catches both `ConnectError` and `TimeoutException`
- Added `-h`/`-p` short flags to start, restart, status
- Fixed streaming memory block: split consolidation/append/trim into separate try/except so a consolidation failure no longer silently skips the other two operations
- Hardened service tests: 10 → 15 tests; fixed false-passing `memories=false` test; added memory-block stripping, base64 image, SSE filter, SSE error event, and `/v1/chat` 500 coverage

---

## 2026-04-19 — v0.12.0 — service image support, SSE streaming, test coverage

- **Image forwarding**: `RunRequest` now accepts `images: list[ImageIn]` (url or base64 data); forwarded to `PriestRequest` as `ImageInput` objects for all `/v1/run` and `/v1/chat` routes
- **SSE streaming routes**: `/v1/run/stream` and `/v1/chat/stream` return `text/event-stream`; each chunk is `data: {"delta": "..."}`, terminal event is `data: [DONE]`; `StreamingStripper` handles memory-block filtering mid-stream; memory consolidation/append/trim runs post-stream
- **Service tests**: `tests/test_service.py` with 10 `TestClient`-based tests covering run, chat, SSE stream, image forwarding, error handling, and session behaviour (mocked engine + store)

---

## 2026-04-19 — v0.11.0 — model rm command

- `priests model rm <provider/model>` removes a model from the list
- If the removed model was the active default, clears the default and prompts to set a new one

---

## 2026-04-19 — v0.10.0 — agentic file reading + search dependency fix

- **Agentic file reading**: model emits `<read_file>/path/to/file</read_file>`; CLI reads up to 100KB, injects content as `user_context`, pops probe exchange, re-prompts — same loop as auto-search
- `StreamingStripper` extended with `<read_file>` block capture
- Tool hint in system prompt now lists both tools (web search + file reading) when applicable
- Refactored agentic re-run into shared `_agentic_rerun()` coroutine to avoid duplication
- **Fixed search dependency**: switched from stale `ddgs` optional extra to `ddgs>=9.14.0` as a core dep; updated `search.py` import accordingly (`duckduckgo_search` → `ddgs`); fixed broken test assertion

---

## 2026-04-19 — v0.9.0 — agentic auto-search loop

- **Auto-search agentic loop**: When the user asks for current information, the model now emits `<search_query>QUERY</search_query>`; the CLI intercepts it, runs the search automatically, pops the probe exchange from session history, and re-prompts the model with the results — transparent to the user, like ChatGPT search
- `StreamingStripper` extended to capture `<search_query>` blocks (same state-machine as `memory_append`/`memory_consolidation`)
- `pop_last_exchange()` added to `extractor.py` — removes the last user+assistant turn pair so the search-probe exchange doesn't pollute conversation history
- Web search system prompt updated: model now emits the tag instead of directing the user to `/search`
- The manual `/search <query>` slash command still works for explicit user-driven searches
- Updated TODO: auto-search agentic loop is done; removed from backlog

---

## 2026-04-19 — v0.8.0 — priest-core v2.0.0 adapter + search prompt fix

- Adapted to `priest-core` v2.0.0 API: renamed `system_context` → `context`, `extra_context` → `user_context`; forwarded new `memory` and `max_system_chars` fields through CLI and service schemas/routes
- Updated `RunRequest` schema (`priests/service/schemas.py`) with `memory`, `user_context`, `max_system_chars`, and backward-compat `system_context` shim
- Fixed `_build_priest_request` in `routes/run.py` to merge `system_context` + `context` → `context`, and pass `memory`/`user_context`/`max_system_chars` to `PriestRequest`
- Fixed hallucinated search: strengthened web search context hint to explicitly state the model has no search tool

---

## 2026-04-12 — priest-core v1.0.0 migration + memory injection fix + web search

- Switched `priest-core` dep from local editable path to `>=1.0.0` on PyPI; removed `[tool.uv.sources]`
- Fixed `priests/service/routes/run.py`: replaced three stale imports (`extract_memories`, `strip_memory_tags`, `write_memories`) removed in v0.5.0 with current memory API
- Fixed memory injection gap: `_build_memory_context` now injects `## Loaded Memories` on all non-consolidation turns (previously memories were only visible to the model during consolidation)
- Added `MemoryConfig.flat_line_cap` — soft on-disk line cap for `user.md`/`notes.md` enforced via consolidation prompt hint
- Fixed `/new` consolidation state bug: `consolidation_done` was not reset between sessions
- Added `WebSearchConfig` (`enabled`, `max_results`) to `AppConfig`; `/search <query>` slash command in chat; results injected into `extra_context` on next turn; model notified via system prompt; powered by `ddgs` optional extra (`priests[search]`)
- Added `tests/test_run_cmd.py`: 10 tests covering `_build_memory_context` non-consolidation branch, `flat_line_cap` hint, and `search()` function (41 total passing)

---

## 2026-04-07 — v0.6.0 memory system hardening + test suite

**Memory system improvements (6.5 → 8/10):**

- `deduplicate_file(path) -> bool` — new public function in `extractor.py`. Strips exact duplicate lines from `user.md` / `notes.md` at session start (case-insensitive, preserves first occurrence and blank lines, skips write if nothing changed to avoid mtime churn)
- `MemoryConfig.context_limit: int = 0` — new config field. Caps the combined character size of all three memory files injected into the system prompt per turn. When exceeded, `auto_short` sections are dropped oldest-first; `user.md` and `notes.md` are never truncated at injection time
- `_truncate_auto_short` — private helper in `run_cmd.py` that drops complete `## YYYY-MM-DD` sections oldest-first, with a hard tail-truncation fallback for single-section edge cases
- Fixed `trim_memories` bug: `while dated` → `while len(dated) > 1` — the last dated section was being silently dropped when the file exceeded `size_limit`
- Fixed dedup ordering: `deduplicate_file` now runs **before** `needs_consolidation` in both `_run_single` and `_run_chat`, so a dedup write does not falsely trigger consolidation on the next session

**Test suite:**
- `tests/bench_memory.py` — 13 performance benchmarks (existing)
- `tests/test_memory.py` — 31 correctness tests covering all public functions: `append_memories`, `apply_consolidation`, `trim_memories`, `needs_consolidation`, `deduplicate_file`, `clean_last_turn`, `StreamingStripper`, `_build_memory_context` context cap, and dedup/sentinel ordering interaction

**Deferred (TODO):**
- Memory system → 9/10: soft on-disk line cap for `user.md`/`notes.md` via consolidation prompt hint; revisit memory injection on non-consolidation turns
- Web search feature

---

## 2026-04-05 — v0.5.0 memory system redesign + CLI refactor

**Memory system redesign:**
- Replaced scattered `auto_{YYYYMMDD}.md` files with single rolling `auto_short.md` (dated `## YYYY-MM-DD` sections)
- Added `user.md` (permanent user facts) and `notes.md` (role constraints) as separate long-term memory files
- `memory.limit` (file count) → `memory.size_limit` (character count, default 50000); `trim_memories` drops oldest dated sections
- Per-turn `<memory_append>{"user":…, "notes":…, "auto_short":…}</memory_append>` block: model appends new facts before streaming response
- First-turn consolidation: if any memory file newer than `.last_consolidated` sentinel, model outputs `<memory_consolidation>` block to rewrite all three files; sentinel touched after all writes so it doesn't re-trigger every session
- `StreamingStripper` rewritten as explicit state machine (replaces regex approach that failed on tag variations); handles both block types, strips from output, captures JSON
- `apply_consolidation` always writes files when key is present (empty string clears); normalizes `auto_short` to dated section format if model omits headers
- Memory instruction in system prompt improved: explicit field scopes, third-person perspective rule, "when in doubt → auto_short" tiebreaker

**CLI refactor:**
- All command groups renamed to singular (`providers` → `provider`)
- Removed `_DefaultRunGroup` shortcut; bare `priests "text"` now errors (`no_args_is_help=True`)
- `--think` and `--memories` changed from bool flags to `str | None` value options with `_parse_bool()`; one-shot defaults `memories=False`
- `priests profile`, `priests model`, `priests provider` bare invocations show current value
- `priests profile init` prompts for name if omitted
- `priests provider <name> list` dynamic routing via `_ProviderGroup(TyperGroup)`
- `priests profile init` and `_bootstrap_profiles` scaffold `memories/user.md`, `memories/notes.md`, `memories/auto_short.md`; `## Memory` section removed from user-editable `RULES.md` stub

---

## Future milestones

### Other deferred items

### Other deferred items
- `service stop` / `service logs` with background daemon mode (PID file)
- `priests models add` — add/configure additional providers after init
- `/new` session command in interactive chat
- Multi-model dispatch, cron task pool, agent orchestration
- OpenAI provider (tracked in `priest` Milestone 2)
- `priest` `SqliteSessionStore.list()` method (priests uses direct aiosqlite query as workaround)
- schedules/plans memory categories (v0.3)

---

## 2026-04-04 — v0.4.4 bug fixes

**Version string out of sync (`__init__.py`):**
- `__init__.py` still had `"0.1.0"`; bumped to match `pyproject.toml`

**Premature `{profile} >` prompt in streaming chat:**
- Label was printed before the first chunk arrived, looking like an input prompt
- Now deferred: printed lazily on first non-empty chunk; fallback prints it if stream is empty

**Words concatenated in streamed output (`StreamingStripper`):**
- `strip_memory_tags()` called `.strip()` per-chunk — ate whitespace at chunk boundaries
- Removed `.strip()` from `strip_memory_tags()`; added `StreamingStripper` class to `extractor.py`
- `StreamingStripper.feed()` buffers from the last `<memory` start, flushes safe prefix
- `StreamingStripper.flush()` drains the buffer after stream ends
- Both `_run_single` and `_run_chat` updated to use `StreamingStripper` instead of per-chunk stripping

**Profile memories not recalled (`priest-core` `context_builder.py`):**
- Memory file contents were injected as unlabeled raw text blocks — model didn't recognize them as facts to recall
- All memory files now combined under a `## Loaded Memories` heading in the system prompt

---

## 2026-04-04 — Provider fixes + proxy support

**Root cause: Python 3.14 breaks httpcore's anyio async TLS backend**
- `AsyncOpenAI` inside `anyio.run()` fails in chat mode (real TTY + prompt_toolkit) due to `httpcore/_backends/anyio.py start_tls` bug on Python 3.14
- Fix: switched `OpenAICompatProvider` to use sync `OpenAI` client in `anyio.to_thread.run_sync()` — uses httpcore sync sockets instead of anyio async TLS
- File: `priest/priest/providers/openai_compat_provider.py`

**Proxy support**
- New `[proxy] url = "..."` section in `priests.toml`
- `use_proxy = true/false` on each provider config (`OpenAICompatConfig`, `AnthropicConfig`)
- `engine_factory.py` resolves proxy URL and passes it to adapter constructors
- `OpenAICompatProvider`, `AnthropicProvider` each accept `proxy: str | None`
- Proxy passed via `httpx.Client(proxy=...)` / `httpx.AsyncClient(proxy=...)`

**`think` parameter scoped to Qwen/Ollama providers only**
- Previously sent `{"think": False}` to all providers — breaks Gemini (400 unknown field)
- `_THINK_PROVIDERS = {"ollama", "bailian", "alibaba_cloud"}` in `run_cmd.py`
- Only those providers get `think` in `provider_options`; all others get `{}`

**Gemini base URL corrected**
- Registry had `/openai/v1`, correct is `/v1beta/openai/`
- Fixed in `registry.py` and updated existing `priests.toml`

---

## 2026-04-03 — Milestone 2: Autonomous memory system

Implemented model-driven persistent memory for `priests` v0.2.

**Memory extraction (`priests/memory/extractor.py`):**
- Regex-based extraction of `<memory>`, `<memory type="user">`, `<memory type="note">` tags from model responses
- Three-file routing: `user.md` (stable facts), `notes.md` (role-important), `auto_YYYYMMDD.md` (daily log)
- Exact-match deduplication (case-insensitive) — no re-saving known facts across sessions
- Placeholder filter (`[Unknown]`, `[Name]`, etc.) — prevents hallucinated values from being written
- `trim_memories()` — deletes oldest `auto_YYYYMMDD.md` files beyond configured limit; `user.md` and `notes.md` are never trimmed
- `clean_last_turn()` — strips memory tags from the last assistant turn in the session store so they don't leak into future context

**Global memory guide (`~/.priests/PRIESTS.md`):**
- Bootstrapped automatically on first run and on v0.1→v0.2 upgrade
- Teaches the model tag syntax, format rules, and when to save via a concrete example
- User-editable; deleting or emptying it disables auto-memory without a code change

**Per-profile config (`priests/profile/config.py`):**
- `profile.toml` scaffolded by `priests profile init` alongside PROFILE.md/RULES.md
- `memories = false` — disables memory loading and saving for tool profiles (formatters, dictionaries, etc.)
- `memories_limit` — overrides the global `[memory].limit` for a specific profile
- Precedence: CLI flag > `profile.toml` > global `priests.toml`

**CLI (`priests/cli/run_cmd.py`):**
- `--memories/--no-memories` flag (default on) — disables memory for a single session
- Chat prompt changed: `you>` → `user >`, `ai>` → `{profile_name} >`
- `[memory saved]` hint shown after turns where facts were written

**HTTP service (`priests/service/routes/run.py`):**
- `?memories=false` query param on `/v1/run` and `/v1/chat`

**Config (`priests/config/model.py`):**
- `MemoryConfig` with `limit: int = 50` — controls how many daily auto files to retain globally

**Verified against:** Ollama + qwen3.5:9b locally. Cross-session recall confirmed (name, preferences, explicit "remember this" requests).

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
