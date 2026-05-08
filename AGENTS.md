# AGENTS

Small repo overlay for `priests`; keep global rules in the global `AGENTS.md`.

## Boundaries

- This is the app layer: CLI, FastAPI service, config, profiles, memory behavior, uploads, search, and React UI.
- Core orchestration belongs in sibling `../priest` unless this repo is adapting to a released `priest-core` API.
- `pyproject.toml` may use editable `../priest` for local development; do not remove that unless doing packaging/release work.
- Runtime data under `~/.priests/` may contain secrets, sessions, uploads, profiles, and memories. Do not read or mutate it unless explicitly needed.

## Change Notes

- Keep provider keys, OAuth tokens, proxy URLs, profile content, session data, and uploads out of git.
- Update `README.md` for user-visible command/config/provider/API changes.
- Update `DEVLOG.md` for meaningful behavior, workflow, or UI changes.
- For UI changes, use the existing React/Vite/Tailwind setup and rebuild `priests/ui/dist/` when shipped assets change.

## Checks

- Python: `uv run pytest tests/ -v`
- Targeted Python: `uv run pytest tests/<file>.py -v`
- CLI smoke: `uv run priests --help`
- UI build: `cd priests/ui && npm run build`
