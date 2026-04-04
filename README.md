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

# Single prompt (three equivalent forms)
priests "your prompt"
priests run "your prompt"
priests run --prompt "your prompt"

# With options
priests run --provider bailian --model qwen-plus --profile friend --session my-session

# Model management
priests model list               # list saved models, shows current default
priests model default            # interactively pick a new default
priests model add                # add a new provider + model

# Provider info
priests providers                # list all providers with configured status
priests providers models <name>  # show known models for a provider

# Profile management
priests profile list
priests profile init "my_profile"

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
limit = 50  # max daily auto memory files to keep per profile; 0 = unlimited
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
    profile.toml    # per-profile settings (memories on/off, limit)
    memories/       # persistent memory files written automatically
      user.md           # stable facts about the user
      notes.md          # role-important things (birthdays, goals, etc.)
      auto_YYYYMMDD.md  # daily observations and conversation context
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

Override the global memory limit for a specific profile:

```toml
memories_limit = 100
```

## Memory system

priests uses a model-driven memory system. After each turn, memory tags emitted by the model are extracted and written to the profile's `memories/` directory. These files are loaded automatically at the start of every future session.

Three memory categories are routed to separate files:

| Tag | File | Use for |
|-----|------|---------|
| `<memory type="user">` | `user.md` | Stable facts: name, hobbies, preferences |
| `<memory type="note">` | `notes.md` | Role-important things: birthdays, goals, key constraints |
| `<memory>` | `auto_YYYYMMDD.md` | Daily observations and session context |

The model decides what is worth remembering based on the profile's character. No configuration is required — the behavior is guided by `~/.priests/PRIESTS.md`, which is bootstrapped automatically on first run.

To disable memory for a session:

```bash
priests run --no-memories
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
