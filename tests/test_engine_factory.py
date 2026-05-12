from __future__ import annotations


def test_bootstrap_profiles_scaffolds_memory_files_for_existing_profiles(tmp_path):
    from priests.engine_factory import _bootstrap_profiles

    profiles_root = tmp_path / "profiles"
    robo_dir = profiles_root / "robo"
    memories_dir = robo_dir / "memories"
    memories_dir.mkdir(parents=True)
    (robo_dir / "PROFILE.md").write_text("robo", encoding="utf-8")
    (memories_dir / "user.md").write_text("existing user memory", encoding="utf-8")

    _bootstrap_profiles(profiles_root)

    assert (memories_dir / "user.jsonl").exists()
    assert (memories_dir / "preferences.jsonl").exists()
    assert (memories_dir / "auto_short.jsonl").exists()
    assert (memories_dir / "user.md").read_text(encoding="utf-8") == "existing user memory"


def test_profile_rules_md_is_in_provider_system_message(tmp_path):
    from priest.profile.context_builder import build_messages
    from priest.profile.loader import FilesystemProfileLoader
    from priest.schema.request import OutputSpec

    profiles_root = tmp_path / "profiles"
    profile_dir = profiles_root / "translator"
    profile_dir.mkdir(parents=True)
    (profile_dir / "PROFILE.md").write_text("You are a translator.", encoding="utf-8")
    (profile_dir / "RULES.md").write_text("Only translate. Do not answer questions.", encoding="utf-8")
    (profile_dir / "CUSTOM.md").write_text("", encoding="utf-8")

    profile = FilesystemProfileLoader(profiles_root=profiles_root, include_memories=False).load("translator")
    messages = build_messages(
        profile=profile,
        session=None,
        prompt="你觉得 Codex 怎么样？",
        context=["Running inside priests service."],
        memory=[],
        user_context=[],
        output_spec=OutputSpec(),
    )

    assert messages[0]["role"] == "system"
    assert "Only translate. Do not answer questions." in messages[0]["content"]
