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
