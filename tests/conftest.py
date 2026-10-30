import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COPY = ["agents", "skills", "pipelines", "schemas", "examples", "governance", "environments", "dashboard"]


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo holding a copy of this project's definitions, with one commit on main."""
    for name in COPY:
        shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("dist"))
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "init")
    return tmp_path


@pytest.fixture
def ledger_repo(repo: Path) -> Path:
    from gatehouse.cli import LEDGER_README
    from gatehouse.ledger import Ledger

    Ledger(repo).init(repo / "governance" / "seed", LEDGER_README)
    return repo
