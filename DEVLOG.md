# DEVLOG

## TODO

- **Memory system → 9/10:** Add a soft on-disk line cap for `user.md`/`notes.md` enforced during consolidation (via the consolidation prompt hint, similar to how `size_limit` hints `auto_short` trimming). Revisit whether memory files should also be injected on normal non-consolidation turns.
- **Web search feature:** Add `web_search` capability.

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
