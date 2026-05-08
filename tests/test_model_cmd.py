from __future__ import annotations

import tomllib

import pytest
import typer


def _write_config(path, profiles_dir, options: list[str] | None = None) -> None:
    opts = options or ["bailian/qwen-plus", "openrouter/openai/gpt-4.1"]
    rendered_opts = ", ".join(repr(o) for o in opts)
    path.write_text(
        f"""
[default]
provider = "ollama"
model = "llama3"
profile = "default"

[models]
options = [{rendered_opts}]

[paths]
profiles_dir = "{profiles_dir}"
""".strip()
    )


def test_model_default_profile_sets_profile_model(tmp_path, monkeypatch):
    from priests.cli import model_cmd

    config_path = tmp_path / "priests.toml"
    profiles_dir = tmp_path / "profiles"
    profile_dir = profiles_dir / "coder"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.toml").write_text("memories = true\n")
    _write_config(config_path, profiles_dir)

    monkeypatch.setattr(model_cmd, "_arrow_select", lambda _prompt, _choices: "bailian/qwen-plus")

    model_cmd.model_default(profile="coder", config_file=config_path)

    data = tomllib.loads((profile_dir / "profile.toml").read_text())
    assert data["provider"] == "bailian"
    assert data["model"] == "qwen-plus"


def test_model_default_profile_use_default_clears_profile_model(tmp_path, monkeypatch):
    from priests.cli import model_cmd

    config_path = tmp_path / "priests.toml"
    profiles_dir = tmp_path / "profiles"
    profile_dir = profiles_dir / "coder"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.toml").write_text(
        'memories = true\nprovider = "bailian"\nmodel = "qwen-plus"\n'
    )
    _write_config(config_path, profiles_dir)

    def fake_select(_prompt, choices):
        assert choices[0].value == model_cmd._USE_GLOBAL_DEFAULT
        assert "Use default (ollama/llama3)" in choices[0].title
        return model_cmd._USE_GLOBAL_DEFAULT

    monkeypatch.setattr(model_cmd, "_arrow_select", fake_select)

    model_cmd.model_default(profile="coder", config_file=config_path)

    data = tomllib.loads((profile_dir / "profile.toml").read_text())
    assert "provider" not in data
    assert "model" not in data
    assert data["memories"] is True


def test_model_default_profile_invalid_name_exits(tmp_path):
    from priests.cli import model_cmd

    config_path = tmp_path / "priests.toml"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    _write_config(config_path, profiles_dir)

    with pytest.raises(typer.Exit) as exc:
        model_cmd.model_default(profile="missing", config_file=config_path)

    assert exc.value.exit_code == 1
