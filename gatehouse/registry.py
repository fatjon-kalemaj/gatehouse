"""Reads agent, skill and pipeline definitions from the repo and pins each to a git version.

The same files are what `ant apply` syncs to Claude Managed Agents, so the version recorded in a run
log (commit SHA plus tree hash of the skill directory) identifies exactly what ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .gitutil import git, rev_parse


@dataclass(frozen=True)
class SkillVersion:
    name: str
    path: str
    commit: str  # last commit that touched the skill directory, or "uncommitted"
    tree: str  # git tree hash of the directory at HEAD (content address), or "uncommitted"
    dirty: bool  # working copy differs from HEAD

    def as_log(self) -> dict:
        return {"skill": self.name, "skill_commit": self.commit, "skill_tree": self.tree, "skill_dirty": self.dirty}


@dataclass
class AgentSpec:
    name: str
    path: str
    description: str
    model: str
    effort: str
    skills: list[str]
    system: str
    roster: list[str] = field(default_factory=list)


@dataclass
class Step:
    agent: str
    produces: str
    consumes: list[str]
    gate: bool = False


@dataclass
class Pipeline:
    name: str
    description: str
    steps: list[Step]
    max_cost_usd: float
    max_duration_s: int


def _frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise ValueError("agent file has no YAML frontmatter")
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm) or {}, body.strip()


def load_agent(path: Path, repo: Path) -> AgentSpec:
    meta, body = _frontmatter(path.read_text())
    model = meta.get("model")
    if isinstance(model, dict):
        model_id, effort = model["id"], model.get("effort", "medium")
    else:
        model_id, effort = model, "medium"
    skills = [Path(s).name for s in meta.get("skills", [])]
    roster = []
    sub = (meta.get("multiagent") or {}).get("subagents") or {}
    for entry in sub.get("predefined_agents", []):
        if isinstance(entry, str) and entry.endswith(".md"):
            roster.append((path.parent / entry).resolve().parent.name)
    return AgentSpec(
        name=meta["name"],
        path=str(path.relative_to(repo)),
        description=meta.get("description", ""),
        model=model_id,
        effort=effort,
        skills=skills,
        system=body,
        roster=roster,
    )


def load_pipeline(path: Path) -> Pipeline:
    data = yaml.safe_load(path.read_text())
    steps = [
        Step(agent=s["agent"], produces=s["produces"], consumes=list(s.get("consumes", [])), gate=bool(s.get("gate")))
        for s in data["steps"]
    ]
    limits = data.get("limits", {})
    return Pipeline(
        name=data["name"],
        description=data.get("description", ""),
        steps=steps,
        max_cost_usd=float(limits.get("max_cost_usd", 5.0)),
        max_duration_s=int(limits.get("max_duration_s", 900)),
    )


class Registry:
    def __init__(self, repo: Path, ref: str = "HEAD"):
        self.repo = Path(repo)
        self.ref = ref  # the main-branch commit whose versions are recorded
        self._versions: dict[str, SkillVersion] = {}
        self.agents: dict[str, AgentSpec] = {}
        for p in sorted((self.repo / "agents").glob("*/agent.md")):
            spec = load_agent(p, self.repo)
            self.agents[spec.name] = spec
        self.pipelines: dict[str, Pipeline] = {
            p.stem: load_pipeline(p) for p in sorted((self.repo / "pipelines").glob("*.yaml"))
        }

    @property
    def orchestrator(self) -> AgentSpec:
        for a in self.agents.values():
            if a.roster:
                return a
        raise LookupError("no orchestrator agent (an agent with a roster) found under agents/")

    def repo_commit(self) -> str:
        return rev_parse(self.repo, self.ref) or "uncommitted"

    def skill_version(self, skill: str) -> SkillVersion:
        if skill not in self._versions:
            self._versions[skill] = self._resolve_skill(skill)
        return self._versions[skill]

    def _resolve_skill(self, skill: str) -> SkillVersion:
        rel = f"skills/{skill}"
        if not (self.repo / rel / "SKILL.md").exists():
            raise LookupError(f"skill {skill!r} not found at {rel}/SKILL.md")
        commit = git(self.repo, "log", "-1", "--format=%H", self.ref, "--", rel, check=False).strip() or "uncommitted"
        tree = git(self.repo, "rev-parse", "--verify", "--quiet", f"{self.ref}:{rel}", check=False).strip() or "uncommitted"
        dirty = self.ref == "HEAD" and bool(git(self.repo, "status", "--porcelain", "--", rel, check=False).strip())
        return SkillVersion(name=skill, path=rel, commit=commit, tree=tree, dirty=dirty)

    def skill_text(self, skill: str) -> str:
        return (self.repo / "skills" / skill / "SKILL.md").read_text()

    def validate(self) -> list[str]:
        """Static checks run in CI: every pipeline step is on the orchestrator's roster, every skill exists."""
        problems = []
        orch = self.orchestrator
        for agent in self.agents.values():
            for s in agent.skills:
                if not (self.repo / "skills" / s / "SKILL.md").exists():
                    problems.append(f"{agent.path}: skill {s!r} has no skills/{s}/SKILL.md")
        for pipe in self.pipelines.values():
            for step in pipe.steps:
                if step.agent not in self.agents:
                    problems.append(f"pipeline {pipe.name}: unknown agent {step.agent!r}")
                elif step.agent not in orch.roster:
                    problems.append(f"pipeline {pipe.name}: {step.agent!r} is not on the orchestrator roster")
        return problems
