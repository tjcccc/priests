from __future__ import annotations

import pytest
import typer


def _write_config(path) -> None:
    path.write_text(
        """
[default]
provider = "ollama"
model = "llama3"
profile = "default"

[providers.ollama]
base_url = "http://ollama.test"
""".strip()
    )


def test_provider_storage_lists_ollama_models(tmp_path, monkeypatch):
    from priests.cli import provider_cmd

    config_path = tmp_path / "priests.toml"
    _write_config(config_path)
    monkeypatch.setattr(
        provider_cmd,
        "fetch_ollama_model_records",
        lambda _base_url: [{"name": "llama3", "size": 1024, "digest": "abcdef123456"}],
    )

    provider_cmd.provider_storage(config_file=config_path)


def test_provider_delete_local_model_requires_existing_model(tmp_path, monkeypatch):
    from priests.cli import provider_cmd

    config_path = tmp_path / "priests.toml"
    _write_config(config_path)
    monkeypatch.setattr(
        provider_cmd,
        "fetch_ollama_model_records",
        lambda _base_url: [{"name": "llama3", "size": 1024}],
    )

    with pytest.raises(typer.Exit) as exc:
        provider_cmd.provider_delete_local_model("missing", yes=True, config_file=config_path)

    assert exc.value.exit_code == 1


def test_provider_delete_local_model_calls_ollama_delete(tmp_path, monkeypatch):
    from priests.cli import provider_cmd

    config_path = tmp_path / "priests.toml"
    _write_config(config_path)
    deleted = {}
    monkeypatch.setattr(
        provider_cmd,
        "fetch_ollama_model_records",
        lambda _base_url: [{"name": "llama3", "size": 1024}],
    )
    monkeypatch.setattr(
        provider_cmd,
        "delete_ollama_model",
        lambda base_url, model: deleted.update({"base_url": base_url, "model": model}),
    )

    provider_cmd.provider_delete_local_model("llama3", yes=True, config_file=config_path)

    assert deleted == {"base_url": "http://ollama.test", "model": "llama3"}
