# priests

CLI tool and HTTP service for [priest](https://github.com/tjcccc/priest) AI orchestration.

## What it does

- `priests run` — interactive chat or single-prompt CLI
- `priests service start` — FastAPI HTTP service (`POST /v1/run`, `POST /v1/chat`, session management)
- Profile management, config management, provider and model setup

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- At least one configured provider (Ollama local, or any supported cloud API)

## Install

### From source

```bash
git clone <this repo>
cd priests
uv sync
```

## Setup

```bash
priests init
```

Walks you through selecting a provider, entering an API key, picking a model, and writing `~/.priests/priests.toml`.

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
priests model add                # add a new provider + model

# Provider info
priests provider                 # show current provider
priests provider list            # list all providers with configured status
priests provider <name> list     # show known models for a provider

# Profile management
priests profile                  # show current profile
priests profile list
priests profile init             # prompts for name
priests profile init my_profile

# Config
priests config show
priests config set default.model qwen-plus

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
| `/exit` | Exit the chat |

Ctrl+J inserts a newline. Enter submits.

## Supported providers

| Key | Provider | Region |
|-----|----------|--------|
| `ollama` | Ollama (local models) | Local |
| `openai` | OpenAI | International |
| `anthropic` | Anthropic Claude | International |
| `gemini` | Google Gemini | International |
| `groq` | Groq | International |
| `openrouter` | OpenRouter | International |
| `minimax` | MiniMax | International |
| `bailian` | Alibaba Bailian | China mainland |
| `alibaba_cloud` | Alibaba Cloud | International |
| `deepseek` | DeepSeek | China mainland |
| `kimi` | Kimi (Moonshot) | China mainland |
| `custom` | Custom OpenAI-compatible endpoint | Any |

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
port = 8777

[proxy]
url = "http://127.0.0.1:7890"

[providers.ollama]
base_url = "http://localhost:11434"

[providers.bailian]
api_key = "sk-..."
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
use_proxy = false

[providers.gemini]
api_key = "AIza..."
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
use_proxy = true

[memory]
size_limit = 50000  # max characters in auto_short.md; 0 = unlimited
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
    memories/       # persistent memory files written automatically
      user.md           # stable facts about the user (permanent)
      notes.md          # role constraints and behavioural context (permanent)
      auto_short.md     # time-sensitive observations, tasks, reminders (rolling)
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

For tool profiles that don't need memory (formatters, dictionaries, etc.), set `memories = false` in `profile.toml`:

```toml
# ~/.priests/profiles/json_master/profile.toml
memories = false
```

## Memory system

priests uses a model-driven memory system. After each turn the model emits a structured block before its response; priests extracts it and writes the content to the profile's `memories/` directory. These files are loaded automatically at the start of every future session.

Three files serve different scopes:

| File | Use for |
|------|---------|
| `user.md` | Stable facts about the user (name, background, permanent preferences) |
| `notes.md` | Behavioural constraints for the profile role |
| `auto_short.md` | Time-sensitive observations, tasks, reminders — rolling, trimmed by `size_limit` |

`auto_short.md` uses dated sections (`## YYYY-MM-DD`). Oldest sections are dropped automatically once the file exceeds `memory.size_limit` characters.

At the start of a session, if any memory file has changed since the last run, the model consolidates all three files before responding — removing redundant or outdated facts.

To disable memory for a single run:

```bash
priests run --memories false
```

To disable permanently for a profile, set `memories = false` in `profile.toml`.

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
```
