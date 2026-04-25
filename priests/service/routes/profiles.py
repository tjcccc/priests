from __future__ import annotations

import re
from pathlib import Path

import tomli_w
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

router = APIRouter()

_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")


def _check_name(name: str) -> None:
    if not _SAFE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid profile name: {name!r}")


def _profiles_dir(request: Request) -> Path:
    return request.app.state.config.paths.profiles_dir.expanduser()


def _read_md(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ProfileFilesOut(BaseModel):
    profile_md: str
    rules_md: str
    custom_md: str
    memories: bool


class ProfileFilesIn(BaseModel):
    profile_md: str | None = None
    rules_md: str | None = None
    custom_md: str | None = None
    memories: bool | None = None


class CreateProfileIn(BaseModel):
    name: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/profiles")
async def list_profiles(request: Request) -> list[str]:
    root = _profiles_dir(request)
    if not root.exists():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and _SAFE.match(d.name))


@router.get("/profiles/{name}", response_model=ProfileFilesOut)
async def get_profile(name: str, request: Request) -> ProfileFilesOut:
    _check_name(name)
    d = _profiles_dir(request) / name
    if not d.exists():
        raise HTTPException(status_code=404, detail=f"Profile {name!r} not found")
    memories = True
    toml_path = d / "profile.toml"
    if toml_path.exists():
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        memories = bool(data.get("memories", True))
    return ProfileFilesOut(
        profile_md=_read_md(d / "PROFILE.md"),
        rules_md=_read_md(d / "RULES.md"),
        custom_md=_read_md(d / "CUSTOM.md"),
        memories=memories,
    )


@router.put("/profiles/{name}", status_code=204)
async def update_profile(name: str, body: ProfileFilesIn, request: Request) -> None:
    _check_name(name)
    d = _profiles_dir(request) / name
    d.mkdir(parents=True, exist_ok=True)
    if body.profile_md is not None:
        (d / "PROFILE.md").write_text(body.profile_md, encoding="utf-8")
    if body.rules_md is not None:
        (d / "RULES.md").write_text(body.rules_md, encoding="utf-8")
    if body.custom_md is not None:
        (d / "CUSTOM.md").write_text(body.custom_md, encoding="utf-8")
    if body.memories is not None:
        toml_path = d / "profile.toml"
        existing: dict = {}
        if toml_path.exists():
            existing = dict(tomllib.loads(toml_path.read_text(encoding="utf-8")))
        existing["memories"] = body.memories
        toml_path.write_bytes(tomli_w.dumps(existing).encode())


@router.post("/profiles", status_code=201)
async def create_profile(body: CreateProfileIn, request: Request) -> dict:
    _check_name(body.name)
    d = _profiles_dir(request) / body.name
    if d.exists():
        raise HTTPException(status_code=409, detail=f"Profile {body.name!r} already exists")
    d.mkdir(parents=True, exist_ok=True)
    (d / "PROFILE.md").write_text("", encoding="utf-8")
    (d / "RULES.md").write_text("", encoding="utf-8")
    (d / "CUSTOM.md").write_text("", encoding="utf-8")
    return {"name": body.name}
