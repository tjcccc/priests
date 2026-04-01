# priests

CLI tool and HTTP service for [priest](https://github.com/tjcccc/priest) AI orchestration.

## What it does

- `priests run` — interactive chat or single-prompt CLI
- `priests service start` — FastAPI HTTP service (`POST /v1/run`, `POST /v1/chat`, session management)
- Profile management, config management, provider setup

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- A running [Ollama](https://ollama.com) instance with at least one model pulled

## Install

### From wheels (recommended for testing)

```bash
pip install priest-0.1.0-py3-none-any.whl priests-0.1.0-py3-none-any.whl
```

Or isolated with `uv tool`:

```bash
uv tool install priests-0.1.0-py3-none-any.whl --find-links .
```

### From source

```bash
git clone <this repo>
cd priests
uv sync
uv pip install -e .
```

## Setup

```bash
priests init
```

Walks you through selecting a provider, picking a local model, and writing `~/.priests/priests.toml`.

## Usage

```bash
# Interactive chat
priests run

# Single prompt (three equivalent forms)
priests "your prompt"
priests run "your prompt"
priests run --prompt "your prompt"

# With options
priests run --provider ollama --model qwen3.5:9b --profile friend --session my-session

# Profile management
priests profile list
priests profile init "my_profile"

# Config
priests config show
priests config set default.model llama3.2:3b

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
| `/think on` | Enable thinking mode (Qwen3/Ollama) |
| `/think off` | Disable thinking mode |
| `/new` | New session *(coming soon)* |
| `/exit` | Exit the chat |

## HTTP API

Start the service with `priests service start`, then:

```
GET  /health
POST /v1/run              single run, no session
POST /v1/chat             session-backed chat
GET  /v1/sessions         list sessions
GET  /v1/sessions/{id}    get session with full turn history
```

## Config file

Location: `~/.priests/priests.toml`

```toml
[default]
provider = "ollama"
model = "qwen3.5:9b"
profile = "default"
think = false

[paths]
profiles_dir = "~/.priests/profiles"
sessions_db  = "~/.priests/sessions.db"

[service]
host = "127.0.0.1"
port = 8777

[providers.ollama]
base_url = "http://localhost:11434"
```

Env var overrides use `PRIESTS_` prefix with `__` for nesting:

```bash
PRIESTS_DEFAULT__MODEL=llama3.2:3b priests run "hello"
PRIESTS_SERVICE__PORT=9000 priests service start
```

## Profiles

Profiles live in `~/.priests/profiles/<name>/` and define behavior context:

```
profiles/
  default/
    PROFILE.md    # identity and persona
    RULES.md      # strict constraints
    CUSTOM.md     # user customization
    memories/     # persistent memory files (.md or .txt)
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
