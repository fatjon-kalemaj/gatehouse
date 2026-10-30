"""The `usage-logs` branch: an append-only, hash-chained ledger kept in the same repo.

Writes never touch the working tree. Each append builds a new tree with git plumbing in a temporary
index and moves the branch with a compare-and-swap `update-ref`, so a concurrent writer can never
silently overwrite another's commit. In the cloud the gateway then pushes the branch; repository
rules reject force pushes and deletions, and `verify()` re-checks the whole history.

Layout of the branch:

    README.md
    governance/allowlist.yaml   who may run which pipeline (mutable, every change audited)
    governance/policy.yaml      budgets, approvals, input screening (mutable, every change audited)
    governance/audit.jsonl      audit trail, append-only, hash-chained
    logs/runs/YYYY-MM.jsonl     one record per run, append-only, hash-chained
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .gitutil import GitError, git, rev_parse, show_file

BRANCH = "usage-logs"
RUNS_PREFIX = "logs/runs/"
AUDIT_PATH = "governance/audit.jsonl"
MUTABLE_PATHS = {"governance/allowlist.yaml", "governance/policy.yaml", "README.md"}
BOT_NAME = "gatehouse-bot"
BOT_EMAIL = "gatehouse-bot@users.noreply.github.com"


def canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return "sha256:" + hashlib.sha256(canonical(body).encode()).hexdigest()


def runs_partition(ts: str) -> str:
    return f"{RUNS_PREFIX}{ts[:7]}.jsonl"


def _is_ancestor(repo: Path, commit: str, ref: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", commit, ref], cwd=repo).returncode == 0


class LedgerConflict(RuntimeError):
    pass


@dataclass
class VerifyReport:
    commits: int = 0
    runs: int = 0
    audit_events: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class Ledger:
    def __init__(self, repo: Path, branch: str = BRANCH):
        self.repo = Path(repo)
        self.branch = branch
        self.ref = f"refs/heads/{branch}"

    # ---------- reading ----------

    def exists(self) -> bool:
        return rev_parse(self.repo, self.ref) is not None

    def tip(self) -> str | None:
        return rev_parse(self.repo, self.ref)

    def read(self, path: str, ref: str | None = None) -> str | None:
        return show_file(self.repo, ref or self.ref, path)

    def list_paths(self, prefix: str, ref: str | None = None) -> list[str]:
        out = git(self.repo, "ls-tree", "-r", "--name-only", ref or self.ref, "--", prefix, check=False)
        return sorted(p for p in out.splitlines() if p)

    @staticmethod
    def _parse_jsonl(text: str | None) -> list[dict]:
        return [json.loads(line) for line in (text or "").splitlines() if line.strip()]

    def runs(self) -> list[dict]:
        """All run records in chain order. Partitions are by run start month, so a backfilled run can
        land in an earlier file than the previous append; `seq` is the order that counts."""
        records = []
        for p in self.list_paths(RUNS_PREFIX):
            records.extend(self._parse_jsonl(self.read(p)))
        return sorted(records, key=lambda r: r.get("seq", 0))

    def audit(self) -> list[dict]:
        return self._parse_jsonl(self.read(AUDIT_PATH))

    def _last(self, stream: str) -> dict | None:
        if stream == "runs":
            recs = self.runs()
            return recs[-1] if recs else None
        recs = self.audit()
        return recs[-1] if recs else None

    # ---------- writing ----------

    def _commit(self, files: dict[str, str], message: str, parent: str | None, when: datetime | None) -> str:
        """Write `files` (path -> full new content) on top of `parent` and return the new commit SHA."""
        stamp = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S%z")
        env = {
            "GIT_AUTHOR_NAME": BOT_NAME,
            "GIT_AUTHOR_EMAIL": BOT_EMAIL,
            "GIT_COMMITTER_NAME": BOT_NAME,
            "GIT_COMMITTER_EMAIL": BOT_EMAIL,
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
        }
        with tempfile.TemporaryDirectory() as tmp:
            env["GIT_INDEX_FILE"] = os.path.join(tmp, "index")
            if parent:
                git(self.repo, "read-tree", parent, env=env)
            for path, content in files.items():
                blob = git(self.repo, "hash-object", "-w", "--stdin", input=content, env=env).strip()
                git(self.repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
            tree = git(self.repo, "write-tree", env=env).strip()
        args = ["commit-tree", tree, "-m", message]
        if parent:
            args[2:2] = ["-p", parent]
        return git(self.repo, *args, env=env).strip()

    def _move(self, new: str, old: str | None) -> None:
        # Compare-and-swap: fails if someone else moved the branch since we read it.
        expected = old or "0" * 40
        try:
            git(self.repo, "update-ref", "-m", "gatehouse append", self.ref, new, expected)
        except GitError as exc:
            raise LedgerConflict(str(exc)) from exc

    def init(self, seed_dir: Path, readme: str, when: datetime | None = None) -> str:
        if self.exists():
            raise RuntimeError(f"branch {self.branch} already exists")
        files = {"README.md": readme, AUDIT_PATH: "", f"{RUNS_PREFIX}.keep": ""}
        for name in ("allowlist.yaml", "policy.yaml"):
            files[f"governance/{name}"] = (Path(seed_dir) / name).read_text()
        commit = self._commit(files, "ledger: initialize usage-logs", None, when)
        self._move(commit, None)
        event = {
            "type": "ledger.initialized",
            "at": (when or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            "actor": BOT_EMAIL,
            "detail": {"governance_files": ["allowlist.yaml", "policy.yaml"]},
        }
        self.append(audit=[event], message="audit: ledger initialized", when=when)
        return self.tip()

    def append(
        self,
        runs: list[dict] | None = None,
        audit: list[dict] | None = None,
        replace: dict[str, str] | None = None,
        message: str = "append",
        when: datetime | None = None,
        retries: int = 3,
    ) -> list[dict]:
        """Append run records and audit events (hash-chained) and optionally replace mutable files.

        Returns the records as written, with `seq`, `prev_hash` and `hash` filled in.
        """
        for _ in range(retries):
            parent = self.tip()
            files: dict[str, str] = {}
            written: list[dict] = []

            last = self._last("runs")
            for rec in runs or []:
                rec = dict(rec)
                rec["seq"] = (last["seq"] + 1) if last else 1
                rec["prev_hash"] = last["hash"] if last else None
                rec["hash"] = record_hash(rec)
                path = runs_partition(rec["started_at"])
                if path not in files:
                    files[path] = self.read(path) or ""
                files[path] += canonical(rec) + "\n"
                written.append(rec)
                last = rec

            last = self._last("audit")
            for ev in audit or []:
                ev = dict(ev)
                ev["seq"] = (last["seq"] + 1) if last else 1
                ev["prev_hash"] = last["hash"] if last else None
                ev["hash"] = record_hash(ev)
                if AUDIT_PATH not in files:
                    files[AUDIT_PATH] = self.read(AUDIT_PATH) or ""
                files[AUDIT_PATH] += canonical(ev) + "\n"
                written.append(ev)
                last = ev

            for path, content in (replace or {}).items():
                if path not in MUTABLE_PATHS:
                    raise ValueError(f"{path} is append-only and cannot be replaced")
                files[path] = content

            commit = self._commit(files, message, parent, when)
            try:
                self._move(commit, parent)
                return written
            except LedgerConflict:
                continue
        raise LedgerConflict(f"could not append to {self.branch} after {retries} attempts")

    # ---------- verification ----------

    def verify(self, since: str | None = None) -> VerifyReport:
        """Check hash chains and that every commit on the branch only appended to append-only files.

        `since` is a checkpoint: a commit (or tag) of this branch that an earlier audit verified. If it is
        no longer an ancestor of the branch tip, the branch history was rewritten after that audit, even if
        the rewrite recomputed every hash.
        """
        report = VerifyReport()
        if not self.exists():
            report.errors.append(f"branch {self.branch} does not exist")
            return report

        if since:
            checkpoint = rev_parse(self.repo, f"{since}^{{commit}}")
            if checkpoint is None:
                report.errors.append(f"checkpoint {since} not found")
            elif not _is_ancestor(self.repo, checkpoint, self.ref):
                report.errors.append(
                    f"history rewritten: checkpoint {since} ({checkpoint[:10]}) is no longer part of {self.branch}"
                )

        for name, records in (("runs", self.runs()), ("audit", self.audit())):
            prev = None
            for i, rec in enumerate(records):
                where = f"{name}[{i}] seq={rec.get('seq')}"
                if rec.get("hash") != record_hash(rec):
                    report.errors.append(f"{where}: hash does not match content")
                expected_seq = (prev["seq"] + 1) if prev else 1
                if rec.get("seq") != expected_seq:
                    report.errors.append(f"{where}: expected seq {expected_seq}")
                if rec.get("prev_hash") != (prev["hash"] if prev else None):
                    report.errors.append(f"{where}: prev_hash does not link to the previous record")
                prev = rec
            if name == "runs":
                report.runs = len(records)
            else:
                report.audit_events = len(records)

        commits = git(self.repo, "rev-list", "--reverse", "--parents", self.ref).split("\n")
        for line in filter(None, commits):
            parts = line.split()
            commit, parents = parts[0], parts[1:]
            report.commits += 1
            if len(parents) > 1:
                report.errors.append(f"{commit[:10]}: merge commits are not allowed on {self.branch}")
                continue
            if not parents:
                continue
            parent = parents[0]
            changed = git(self.repo, "diff-tree", "--no-commit-id", "-r", "--name-status", parent, commit)
            touched_governance = False
            audit_grew = False
            for row in filter(None, changed.split("\n")):
                status, path = row.split("\t", 1)
                if path in MUTABLE_PATHS:
                    touched_governance |= path.startswith("governance/")
                    continue
                if status == "D":
                    report.errors.append(f"{commit[:10]}: deleted append-only file {path}")
                elif status == "M":
                    old = self.read(path, parent) or ""
                    new = self.read(path, commit) or ""
                    if not new.startswith(old):
                        report.errors.append(f"{commit[:10]}: rewrote append-only file {path}")
                    elif path == AUDIT_PATH and len(new) > len(old):
                        audit_grew = True
                elif status == "A" and path == AUDIT_PATH:
                    audit_grew = True
            if touched_governance and not audit_grew:
                report.errors.append(f"{commit[:10]}: governance file changed without an audit event")
        return report
