#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from priest import PriestConfig, PriestRequest, SessionRef  # noqa: E402

from priests.config.model import AppConfig  # noqa: E402
from priests.engine_factory import build_engine  # noqa: E402
from priests.memory.extractor import (  # noqa: E402
    AUTO_JSONL_FILE,
    PREFERENCES_JSONL_FILE,
    USER_JSONL_FILE,
    StreamingStripper,
    append_memories,
    apply_memory_proposals,
    assemble_memory_entries,
    build_memory_instructions,
    clean_last_turn,
    save_memories,
    trim_memories,
)


TIME_3_PM = r"(?:\b(?:3|three)(?::00)?\s*(?:p\.?\s*m\.?|pm)\b|\b15:00\b)"
TIME_4_PM = r"(?:\b(?:4|four)(?::00)?\s*(?:p\.?\s*m\.?|pm)\b|\b16:00\b)"


@dataclasses.dataclass(frozen=True)
class ReplyCheck:
    any_of: tuple[str, ...] = ()
    all_of: tuple[str, ...] = ()
    none_of: tuple[str, ...] = ()
    description: str = ""


@dataclasses.dataclass(frozen=True)
class MemoryCheck:
    kind: str | None
    all_of: tuple[str, ...]
    description: str
    status: str = "active"
    max_priority: int | None = None
    min_confidence: float | None = None


@dataclasses.dataclass(frozen=True)
class ForbiddenMemoryCheck:
    kind: str | None
    pattern: str
    description: str
    status: str = "active"
    unless: str | None = None


@dataclasses.dataclass(frozen=True)
class EvalStep:
    name: str
    prompt: str
    reply: ReplyCheck | None = None
    memory: tuple[MemoryCheck, ...] = ()
    forbidden_memory: tuple[ForbiddenMemoryCheck, ...] = ()


@dataclasses.dataclass
class StepResult:
    name: str
    prompt: str
    response: str
    passed: bool
    failures: list[str]
    save_blocks: int
    memory_rows: list[dict[str, Any]]


def _cases() -> list[EvalStep]:
    return [
        EvalStep(
            name="save_meeting",
            prompt="Memory test: I have a project meeting tomorrow at 3 p.m.",
            memory=(
                MemoryCheck(
                    kind="auto_short",
                    all_of=(r"\bproject\b", r"\bmeeting\b", TIME_3_PM, r"(tomorrow|2026-05-10|may\s+10)"),
                    description="project meeting tomorrow at 3 p.m. saved as short-term memory",
                    max_priority=3,
                    min_confidence=0.6,
                ),
            ),
        ),
        EvalStep(
            name="save_editor",
            prompt="Memory test: My favorite editor is Neovim.",
            memory=(
                MemoryCheck(
                    kind="user",
                    all_of=(r"\bneovim\b",),
                    description="favorite editor saved as user memory",
                    max_priority=3,
                    min_confidence=0.6,
                ),
            ),
        ),
        EvalStep(
            name="save_style",
            prompt="Memory test: I prefer short, normal conversation replies.",
            memory=(
                MemoryCheck(
                    kind="preferences",
                    all_of=(r"\b(short|brief|concise)\b", r"\b(replies|answers|conversation)\b"),
                    description="short conversational reply preference saved",
                    max_priority=3,
                    min_confidence=0.6,
                ),
            ),
        ),
        EvalStep(
            name="distractor",
            prompt="Unrelated check: what is 2 + 2? Please answer with only the number.",
            reply=ReplyCheck(all_of=(r"\b4\b",), description="answers unrelated prompt correctly"),
        ),
        EvalStep(
            name="recall_meeting",
            prompt="Sorry, I forgot the project meeting time. Do you remember?",
            reply=ReplyCheck(
                all_of=(TIME_3_PM,),
                none_of=(r"\b10\s*(?:a\.?\s*m\.?|am|p\.?\s*m\.?|pm)\b", r"\bwhat meeting\b", r"\b(do not|don't)\s+know\b"),
                description="recalls project meeting at 3 p.m.",
            ),
        ),
        EvalStep(
            name="recall_editor",
            prompt="Which editor do I like?",
            reply=ReplyCheck(all_of=(r"\bneovim\b",), description="recalls favorite editor"),
        ),
        EvalStep(
            name="recall_style",
            prompt="Please answer briefly: what response style do I prefer?",
            reply=ReplyCheck(
                any_of=(r"\bshort\b", r"\bbrief\b", r"\bconcise\b"),
                description="recalls short-answer preference",
            ),
        ),
        EvalStep(
            name="correct_meeting",
            prompt="Correction for the memory test: the project meeting is at 4 p.m., not 3 p.m.",
            memory=(
                MemoryCheck(
                    kind="auto_short",
                    all_of=(r"\bproject\b", r"\bmeeting\b", TIME_4_PM),
                    description="corrected project meeting at 4 p.m. saved",
                    max_priority=3,
                    min_confidence=0.6,
                ),
            ),
            forbidden_memory=(
                ForbiddenMemoryCheck(
                    kind="auto_short",
                    pattern=TIME_3_PM,
                    unless=r"\bnot\s+(?:at\s+)?3\b",
                    description="old active 3 p.m. meeting memory should be superseded or clearly negated",
                ),
            ),
        ),
        EvalStep(
            name="recall_corrected_meeting",
            prompt="What time is the project meeting now?",
            reply=ReplyCheck(
                all_of=(TIME_4_PM,),
                none_of=(r"\b10\s*(?:a\.?\s*m\.?|am|p\.?\s*m\.?|pm)\b",),
                description="recalls corrected project meeting at 4 p.m.",
            ),
        ),
        EvalStep(
            name="unknown_name",
            prompt="What is my name?",
            reply=ReplyCheck(
                any_of=(r"\b(do not|don't|not)\s+know\b", r"\bhaven't told\b", r"\bnot sure\b", r"\byou have not\b"),
                none_of=(r"\bjack\b", r"\btao\b", r"\balice\b", r"\bbob\b"),
                description="does not invent an unknown name",
            ),
        ),
    ]


def _matches(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _memory_rows(memories_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for filename in (USER_JSONL_FILE, PREFERENCES_JSONL_FILE, AUTO_JSONL_FILE):
        for row in _load_jsonl(memories_dir / filename):
            row = dict(row)
            row["_file"] = filename
            rows.append(row)
    return rows


def _check_reply(check: ReplyCheck, response: str) -> list[str]:
    failures: list[str] = []
    if check.any_of and not any(_matches(pattern, response) for pattern in check.any_of):
        failures.append(f"reply did not match any expected pattern for {check.description}: {check.any_of}")
    for pattern in check.all_of:
        if not _matches(pattern, response):
            failures.append(f"reply missing expected pattern for {check.description}: {pattern}")
    for pattern in check.none_of:
        if _matches(pattern, response):
            failures.append(f"reply included forbidden pattern for {check.description}: {pattern}")
    return failures


def _matching_memory(rows: list[dict[str, Any]], check: MemoryCheck) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for row in rows:
        if check.kind and row.get("kind") != check.kind:
            continue
        if check.status and row.get("status", "active") != check.status:
            continue
        text = str(row.get("text", ""))
        if not all(_matches(pattern, text) for pattern in check.all_of):
            continue
        if check.max_priority is not None and int(row.get("priority", 99)) > check.max_priority:
            continue
        if check.min_confidence is not None and float(row.get("confidence", 0)) < check.min_confidence:
            continue
        matches.append(row)
    return matches


def _check_memory(rows: list[dict[str, Any]], checks: tuple[MemoryCheck, ...]) -> list[str]:
    failures: list[str] = []
    for check in checks:
        if not _matching_memory(rows, check):
            failures.append(f"memory missing: {check.description}")
    return failures


def _check_forbidden_memory(rows: list[dict[str, Any]], checks: tuple[ForbiddenMemoryCheck, ...]) -> list[str]:
    failures: list[str] = []
    for check in checks:
        for row in rows:
            if check.kind and row.get("kind") != check.kind:
                continue
            if check.status and row.get("status", "active") != check.status:
                continue
            text = str(row.get("text", ""))
            if _matches(check.pattern, text) and not (check.unless and _matches(check.unless, text)):
                failures.append(f"forbidden memory still active: {check.description}: {text}")
    return failures


def _write_profile(profiles_dir: Path, profile: str, provider: str, model: str) -> Path:
    profile_dir = profiles_dir / profile
    memories_dir = profile_dir / "memories"
    memories_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "PROFILE.md").write_text(
        "# Memory Eval\n\nYou are a concise assistant participating in a live memory evaluation.\n",
        encoding="utf-8",
    )
    (profile_dir / "RULES.md").write_text(
        "# Rules\n\n"
        "- Reply naturally and briefly.\n"
        "- Use remembered facts only when they are relevant.\n"
        "- Do not invent unknown personal details.\n",
        encoding="utf-8",
    )
    (profile_dir / "CUSTOM.md").write_text("", encoding="utf-8")
    (profile_dir / "profile.toml").write_text(
        f'memories = true\nprovider = "{provider}"\nmodel = "{model}"\n',
        encoding="utf-8",
    )
    for filename in (USER_JSONL_FILE, PREFERENCES_JSONL_FILE, AUTO_JSONL_FILE):
        (memories_dir / filename).write_text("", encoding="utf-8")
    return memories_dir


def _build_config(args: argparse.Namespace, root: Path, profile: str) -> AppConfig:
    config = AppConfig.model_validate(
        {
            "default": {
                "provider": args.provider,
                "model": args.model,
                "profile": profile,
                "think": args.thinking,
                "timeout_seconds": args.timeout,
                "max_output_tokens": args.max_output_tokens,
            },
            "paths": {
                "profiles_dir": str(root / "profiles"),
                "sessions_db": str(root / "sessions.db"),
                "uploads_dir": str(root / "uploads"),
            },
            "memory": {
                "size_limit": args.size_limit,
                "context_limit": args.context_limit,
            },
        }
    )
    if args.provider == "ollama" and args.base_url:
        config.providers.ollama.base_url = args.base_url
    return config


def _provider_options(args: argparse.Namespace) -> dict[str, Any]:
    if args.provider in {"ollama", "bailian", "alibaba_cloud"}:
        return {"think": args.thinking}
    return {"think": True} if args.thinking else {}


def _visible_and_controls(text: str) -> tuple[str, StreamingStripper]:
    stripper = StreamingStripper()
    visible = stripper.feed(text)
    visible += stripper.flush()
    return visible.strip(), stripper


async def _apply_memory_controls(
    *,
    stripper: StreamingStripper,
    memories_dir: Path,
    session_id: str,
    store: Any,
    size_limit: int,
) -> int:
    await clean_last_turn(store, session_id)
    saved = 0
    for payload_text, writer in (
        (stripper.save_json, save_memories),
        (stripper.append_json, append_memories),
        (stripper.proposal_json, apply_memory_proposals),
    ):
        if not payload_text:
            continue
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        writer(memories_dir, payload, session_id=session_id)
        saved += 1
    trim_memories(memories_dir, size_limit)
    return saved


async def run_eval(args: argparse.Namespace) -> tuple[list[StepResult], Path]:
    root = Path(args.workdir).expanduser() if args.workdir else Path(tempfile.mkdtemp(prefix="priests-memory-eval-"))
    root.mkdir(parents=True, exist_ok=True)
    profile = args.profile or f"memory_eval_{uuid.uuid4().hex[:8]}"
    profiles_dir = root / "profiles"
    memories_dir = _write_profile(profiles_dir, profile, args.provider, args.model)
    config = _build_config(args, root, profile)
    engine, store = await build_engine(config)

    base_context = [
        "Running priests live memory evaluation.",
        "For this evaluation, today's date is 2026-05-09. Tomorrow is 2026-05-10.",
        "When the user provides a fact worth remembering, follow the memory policy exactly.",
        "Always include a visible natural-language response after any hidden memory block.",
        build_memory_instructions(),
    ]
    session_id = f"memory-eval-{uuid.uuid4().hex}"
    results: list[StepResult] = []

    async with store:
        for step in _cases():
            request = PriestRequest(
                config=PriestConfig(
                    provider=args.provider,
                    model=args.model,
                    timeout_seconds=args.timeout,
                    max_output_tokens=args.max_output_tokens,
                    max_system_chars=args.max_system_chars,
                    provider_options=_provider_options(args),
                ),
                profile=profile,
                prompt=step.prompt,
                session=SessionRef(id=session_id, create_if_missing=True),
                context=base_context,
                memory=assemble_memory_entries(
                    memories_dir,
                    args.context_limit,
                    thinking=args.thinking,
                    prompt=step.prompt,
                ),
            )
            response = await engine.run(request)
            if response.error:
                visible = f"ERROR {response.error.code}: {response.error.message}"
                stripper = StreamingStripper()
                save_blocks = 0
            else:
                visible, stripper = _visible_and_controls(response.text or "")
                save_blocks = await _apply_memory_controls(
                    stripper=stripper,
                    memories_dir=memories_dir,
                    session_id=session_id,
                    store=store,
                    size_limit=args.size_limit,
                )

            rows = _memory_rows(memories_dir)
            failures: list[str] = []
            if response.error:
                failures.append("model request failed")
            if step.reply:
                failures.extend(_check_reply(step.reply, visible))
            failures.extend(_check_memory(rows, step.memory))
            failures.extend(_check_forbidden_memory(rows, step.forbidden_memory))

            result = StepResult(
                name=step.name,
                prompt=step.prompt,
                response=visible,
                passed=not failures,
                failures=failures,
                save_blocks=save_blocks,
                memory_rows=rows,
            )
            results.append(result)
            if args.stop_on_fail and failures:
                break

    return results, root


def _print_report(results: list[StepResult], root: Path, verbose: bool) -> None:
    passed = sum(1 for result in results if result.passed)
    print(f"Memory eval workspace: {root}")
    print(f"Result: {passed}/{len(results)} passed")
    print()

    for idx, result in enumerate(results, start=1):
        status = "PASS" if result.passed else "FAIL"
        print(f"{idx:02d}. {status} {result.name}")
        print(f"    prompt: {result.prompt}")
        print(f"    reply:  {result.response}")
        print(f"    memory control blocks applied: {result.save_blocks}")
        if result.failures:
            for failure in result.failures:
                print(f"    - {failure}")
        if verbose or result.failures:
            active = [row for row in result.memory_rows if row.get("status", "active") == "active"]
            print(f"    active memory rows: {len(active)}")
            for row in active[-6:]:
                print(
                    "      "
                    + json.dumps(
                        {
                            "kind": row.get("kind"),
                            "priority": row.get("priority"),
                            "confidence": row.get("confidence"),
                            "text": row.get("text"),
                        },
                        ensure_ascii=False,
                    )
                )
        print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a live priests memory evaluation against a real model.")
    parser.add_argument("--provider", default="ollama", help="Provider name. Default: ollama")
    parser.add_argument("--model", default="gemma4:e4b", help="Model name. Default: gemma4:e4b")
    parser.add_argument("--base-url", default=None, help="Optional Ollama base URL override.")
    parser.add_argument("--profile", default=None, help="Temp profile name. Defaults to memory_eval_<random>.")
    parser.add_argument("--workdir", default=None, help="Workspace for temp profiles/sessions. Defaults to /tmp and is removed unless --keep is set.")
    parser.add_argument("--keep", action="store_true", help="Keep the eval workspace after the run.")
    parser.add_argument("--verbose", action="store_true", help="Print active memory rows after every step.")
    parser.add_argument("--stop-on-fail", action="store_true", help="Stop at the first failed step.")
    parser.add_argument("--thinking", action="store_true", help="Enable thinking mode and recall priority 0..10.")
    parser.add_argument("--context-limit", type=int, default=12000, help="Memory context character budget.")
    parser.add_argument("--size-limit", type=int, default=50000, help="auto_short.jsonl character budget.")
    parser.add_argument("--timeout", type=float, default=180.0, help="Provider timeout in seconds.")
    parser.add_argument("--max-output-tokens", type=int, default=512, help="Max output tokens per model response.")
    parser.add_argument("--max-system-chars", type=int, default=None, help="Optional priest-core system prompt budget.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root: Path | None = None
    try:
        results, root = asyncio.run(run_eval(args))
        _print_report(results, root, args.verbose)
        return 0 if all(result.passed for result in results) else 1
    finally:
        if root is not None and not args.keep and args.workdir is None:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
