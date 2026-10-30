"""Thin wrapper over the git CLI. Everything Gatehouse does to git goes through here."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, input: str | None = None, env: dict | None = None, check: bool = True) -> str:
    full_env = {**os.environ, **(env or {})}
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input,
        capture_output=True,
        text=True,
        env=full_env,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def rev_parse(repo: Path, ref: str) -> str | None:
    out = git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).strip()
    return out or None


def show_file(repo: Path, ref: str, path: str) -> str | None:
    proc = subprocess.run(["git", "cat-file", "-p", f"{ref}:{path}"], cwd=repo, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def head_commit(repo: Path) -> str | None:
    return rev_parse(repo, "HEAD")
