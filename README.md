# priests

CLI-first showcase chat app for [priest](https://github.com/tjcccc/priest), centered on **spmem (Structured Personal Memory)** and reusable profiles. The CLI is the core showcase surface, the FastAPI HTTP service can power other apps, and the web UI is a browser showcase built on that service.

## Core ideas

- **spmem (Structured Personal Memory)** — profile-scoped memory for user facts, preferences, and short-term commitments. It stores rich JSONL records, recalls selectively, and keeps prompt context small.
- **Profiles** — reusable chat contexts with their own prompts, rules, model overrides, sessions, and memories.

## What it does

- `priests run` — interactive chat or single-prompt CLI
- `priests service start` — FastAPI HTTP service (`POST /v1/run`, `POST /v1/chat`, session management)
- Web UI — browser chat app at `/ui` with profiles, sessions, provider/model config, uploads, and memory behavior
- Profile management, config management, provider and model setup

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- At least one configured provider (Ollama local, or any supported cloud API)

## Install

### From source

```bash
git clone https://github.com/tjcccc/priests
cd priests
uv sync
```

## Setup

```bash
priests init
```

Walks you through selecting a provider, entering an API key, picking a model, and writing `~/.priests/priests.toml`.

## Local development

```bash
uv sync
uv run priests init
uv run priests service start
```

Open `http://localhost:9000/ui`.

When changing UI assets:

```bash
cd priests/ui
npm install
npm run build
cd ../..
uv run priests service start
```

## Usage

```bash
# Interactive chat
priests run

# Single prompt
priests run "your prompt"

# With options
priests run --provider bailian --model qwen-plus --profile friend --session my-session
priests run "your prompt" --think true --memories false

# Model management
priests model                    # show current default model
priests model list               # list saved models
priests model default            # interactively pick a new default
priests model default --profile coder  # set/clear the model override for a profile
priests model add                # add a new provider + model
priests model validate           # validate the default provider/model
priests model validate ollama/qwen3:8b

# Provider info
priests provider                 # show current provider
priests provider list            # list all providers with configured status
priests provider <name> list     # show known models for a provider
priests provider status          # show configured/reachable provider status
priests provider storage         # list local Ollama model storage
priests provider delete-local-model qwen3:8b --yes

# Profile management
priests profile                  # show current profile
priests profile list
priests profile init             # prompts for name
priests profile init my_profile

# Config
priests config show
priests config set default.model qwen-plus
priests config export ~/priests-backup.zip
priests config import ~/priests-backup.zip --overwrite

# HTTP service (foreground)
priests service start
priests service status

# Help
priests --help
priests <command> --help
```

## Chat commands

Inside `priests run` interactive mode:

| Command | Description |
|---|---|
| `/help` | Show available commands |
| `/think on` | Enable thinking mode (Qwen3 / Ollama) |
| `/think off` | Disable thinking mode |
| `/new` | Start a new session |
| `/search <query>` | Run a web search; results injected into your next message (requires `priests[search]`) |
| `/remember <text>` | Save text directly to short-term memory (`auto_short.jsonl`) |
| `/remember user <text>` | Save approved durable user memory (`user.jsonl`) |
| `/remember pref <text>` | Save approved durable preference memory (`preferences.jsonl`) |
| `/forget <query>` | Soft-forget matching active memory by superseding it |
| `/delete-memory <query>` | Permanently delete matching JSONL memory records |
| `/exit` | Exit the chat |

Ctrl+J inserts a newline. Enter submits.

## Supported providers

| Key | Provider | Type | Region |
|-----|----------|------|--------|
| `ollama` | Ollama | Local | Local |
| `llamacpp` | llama.cpp | Local | Local |
| `lmstudio` | LM Studio | Local | Local |
| `rapidmlx` | Rapid-MLX | Local | Local |
| `openai` | OpenAI | API | International |
| `anthropic` | Anthropic Claude | API | International |
| `gemini` | Google Gemini | API | International |
| `groq` | Groq | API | International |
| `openrouter` | OpenRouter (gateway) | API | International |
| `deepseek` | DeepSeek | API | China mainland |
| `mistral` | Mistral AI | API | International |
| `together` | Together AI | API | International |
| `perplexity` | Perplexity | API | International |
| `cohere` | Cohere | API | International |
| `minimax` | MiniMax | API | International |
| `bailian` | Alibaba Bailian | API | China mainland |
| `alibaba_cloud` | Alibaba Cloud | API | International |
| `kimi` | Kimi (Moonshot) | API | China mainland |
| `github_copilot` | GitHub Copilot | OAuth | International |
| `chatgpt` | ChatGPT (OpenAI OAuth) | OAuth | International |
| `custom` | Custom OpenAI-compatible endpoint | API | Any |

Local providers (Ollama, llama.cpp, LM Studio, Rapid-MLX) require no API key and fetch available models automatically from the running server.

GitHub Copilot OAuth uses the config UI device flow. After authorization, priests stores the GitHub device token and refreshes the short-lived Copilot IDE token before chat requests.

## Config file

Location: `~/.priests/priests.toml`

```toml
[default]
provider = "bailian"
model = "qwen-plus"
profile = "default"
think = false

[models]
options = [
    "bailian/qwen-plus",
    "gemini/gemini-2.5-flash",
]

[paths]
profiles_dir = "~/.priests/profiles"
sessions_db  = "~/.priests/sessions.db"

[service]
host = "127.0.0.1"
port = 9000

[proxy]
url = "http://127.0.0.1:7890"

[providers.ollama]
base_url = "http://localhost:11434"

[providers.rapidmlx]
base_url = "http://localhost:8000/v1"

[providers.bailian]
api_key = "sk-..."
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
use_proxy = false

[providers.gemini]
api_key = "AIza..."
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
use_proxy = true

[memory]
size_limit = 50000  # max characters in auto_short.jsonl; 0 = unlimited
context_limit = 2400  # max memory chars injected per turn; 0 = explicit unlimited
```

### Proxy

Set `[proxy] url` to your local proxy (e.g. Clash, Shadowsocks). Then set `use_proxy = true` on each provider that needs it. Providers with `use_proxy = false` (the default) connect directly even when `[proxy]` is configured.

Env var overrides use `PRIESTS_` prefix with `__` for nesting:

```bash
PRIESTS_DEFAULT__MODEL=qwen-plus priests run "hello"
PRIESTS_SERVICE__PORT=9000 priests service start
```

## Profiles

Profiles live in `~/.priests/profiles/<name>/` and define behavior context:

```
profiles/
  default/
    PROFILE.md      # identity and persona
    RULES.md        # behavior and constraints (plain language)
    CUSTOM.md       # user customization
    profile.toml    # per-profile settings (memories on/off)
    memories/       # persistent memory files assembled by priests
      user.jsonl           # stable facts about the user
      preferences.jsonl    # approved user preferences
      auto_short.jsonl     # time-sensitive observations, tasks, reminders
      user.md              # legacy read-only fallback
      preferences.md       # legacy read-only fallback
      auto_short.md        # legacy read-only fallback
```

Create a new profile:

```bash
priests profile init "english_teacher"
# edit ~/.priests/profiles/english_teacher/PROFILE.md
```

Use it:

```bash
priests run --profile english_teacher
```

Rename or delete non-default profiles from the Config page → Profile Configuration section, or manage them directly in `~/.priests/profiles/`.

For tool profiles that don't need memory (formatters, dictionaries, etc.), set `memories = false` in `profile.toml`:

```toml
# ~/.priests/profiles/json_master/profile.toml
memories = false
```

Profiles can also override the global default model by setting both `provider` and `model`.
Leave them unset to keep using `[default] provider` and `[default] model`.

Set or clear the override from the CLI:

```bash
priests model default --profile coder
```

```toml
# ~/.priests/profiles/coder/profile.toml
provider = "bailian"
model = "qwen-plus"
memories = true
```

## spmem: Structured Personal Memory

spmem is the profile-scoped memory layer owned by priests. It passes assembled memory to `priest` through the special request `memory` lane while keeping the core `priest` framework generic; `priest` does not decide what `user.jsonl`, `preferences.jsonl`, or `auto_short.jsonl` mean.

Profile files and memory files have different authority:

| File | Use for |
|------|---------|
| `PROFILE.md` | Assistant identity/persona |
| `RULES.md` | Human-authored hard behavior rules |
| `CUSTOM.md` | Human-authored profile setup/context |
| `memories/user.jsonl` | Approved stable facts about the user |
| `memories/preferences.jsonl` | Approved stable user preferences, lower priority than profile docs |
| `memories/auto_short.jsonl` | Time-sensitive observations, tasks, reminders, current-session context |
| `memories/*.md` | Legacy read-only fallback memory files |

Legacy `memories/notes.md` is still read if present, but priests treats it as read-only legacy memory and no longer writes to it automatically.

Each JSONL memory entry stores text plus metadata such as `priority`, `confidence`, `stability`, `source`, timestamps, optional `conflict_key`, and supersession status. Storage is intentionally richer than prompt injection: normal prompts receive compact natural-language bullets, not full JSON metadata. Memory is context, not authority; human-authored profile files and the current user message outrank it.

Priority `0` is highest and is reserved for rare, explicit, stable identity facts such as the user's name. Application code downgrades ordinary preferences and time-sensitive facts that a model tries to save as priority `0`.

| Policy | Behavior |
|--------|----------|
| Auto-applied short-term | Structured `auto_short` entries |
| Auto-applied durable | Structured `user` / `preferences` entries |
| Recall budget | Normal mode recalls priority `0..3`; thinking mode recalls priority `0..10`; simple greetings recall only priority `0`; `memory.context_limit` is the final hard budget |
| Never auto-written | `PROFILE.md`, `RULES.md`, `CUSTOM.md`, `profile.toml`, legacy `notes.md` |

The model emits hidden `<memory_save>{...}</memory_save>` JSON blocks for saves and `<memory_forget>{...}</memory_forget>` blocks for explicit forget requests. priests strips those blocks from visible replies and visible session history, including streaming responses where tags may be split across chunks. It validates the structured entries, deduplicates exact matches, canonicalizes compatible conflict-key aliases, supersedes matching open-schema `conflict_key` slots such as `user.favorite_color`, and trims low-priority short-term entries once `auto_short.jsonl` exceeds `memory.size_limit`.

As a reliability backstop, priests also applies conservative code-side extraction for explicit high-value facts in the user prompt, such as names, favorite/preferred values, response-style preferences, and meeting times. This keeps memory behavior consistent across profiles and smaller models even when the model forgets to emit a hidden save block.

Interactive chat includes explicit controls:

```text
/remember <text>       Save short-term memory
/remember user <text>  Save durable user memory
/remember pref <text>  Save durable preference memory
/forget <query>        Soft-forget active memory by text or conflict key
/delete-memory <query> Permanently delete matching JSONL memory records
```

`/forget` preserves the JSONL audit trail by marking matching active records as `superseded`, so they are excluded from recall but remain inspectable. `/delete-memory` physically removes matching structured JSONL records for the current profile. Legacy `.md` memory files are read-only fallback inputs and must be edited manually if they contain matching text.

Run the live memory eval against a local model:

```bash
uv run python scripts/memory_eval.py --provider ollama --model gemma4:e4b --suite professional --json-report /tmp/priests-memory-eval.json --keep --verbose
```

The eval creates a temporary profile/session, sends a fixed prompt sequence, checks both visible replies and JSONL memory state, reports injected memory chars, and exits non-zero when any case fails. Use pytest as the hard deterministic gate; the live eval is release evidence for model cooperation.

To disable memory for a single run:

```bash
priests run --memories false
```

To disable permanently for a profile, set `memories = false` in `profile.toml`.

## Web UI

Start the service with `priests service start` and open `http://localhost:9000/ui`.

- **Chat**: streaming responses, markdown rendering, image attach (drag-and-drop or click), per-turn model and timing info
- **First-run state**: clearer no-model and new-chat states, with visible slow-response and error feedback
- **Sessions**: sidebar with per-profile session lists; pin, rename, and delete sessions
- **Config page** (`/ui/config`): full settings UI with section nav
  - Defaults: provider + model select, profile, timeout, thinking mode
  - Profile Configuration: editor for PROFILE.md / RULES.md / CUSTOM.md per profile; rename and delete profiles
  - Model Configuration: saved model list grouped by provider type (local / API / OAuth), with provider/model validation before save
  - Providers: per-provider API key, base URL, proxy toggle, and health status
  - Local Models: Ollama model storage listing and guarded local delete
  - Memory, Web Search, Service, Proxy, Paths sections — all editable with hot-reload (no restart required except host/port)

## HTTP API

Start the service with `priests service start`, then:

```
GET  /health
POST /v1/run                        single run, no session
POST /v1/run?memories=false         single run, memory disabled
POST /v1/chat                       session-backed chat
POST /v1/chat?memories=false        session-backed chat, memory disabled
GET  /v1/sessions                   list sessions
GET  /v1/sessions/{id}              get session with full turn history
GET  /v1/config                     get full config (API keys masked)
PATCH /v1/config                    partial config update (dotted keys)
GET  /v1/providers/{name}/models    list available models for a provider
GET  /v1/providers/status           provider config and local reachability status
POST /v1/providers/validate         validate a provider/model pair
GET  /v1/providers/{name}/storage   list local model storage where supported
DELETE /v1/providers/{name}/models  delete a local model where supported
GET  /v1/profiles                   list profiles
GET  /v1/profiles/{name}            get profile files
PUT  /v1/profiles/{name}            update profile files
POST /v1/profiles/{name}/memories/delete  permanently delete matching JSONL memory
POST /v1/profiles                   create profile
POST /v1/profiles/{name}/rename     rename profile
DELETE /v1/profiles/{name}          delete profile
```
