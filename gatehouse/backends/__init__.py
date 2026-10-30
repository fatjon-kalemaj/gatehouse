"""Execution backends. The gateway hands a run to one of these after policy checks pass."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..orchestrator import OrchestratorResult, SequentialOrchestrator
from ..pricing import cost_usd
from ..registry import Pipeline, Registry


@dataclass
class RunContext:
    run_id: str
    registry: Registry
    pipeline: Pipeline
    input_path: str
    started_at: datetime
    budget_usd: float
    run_dir: Path
    inject: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecResult:
    result: OrchestratorResult
    orchestrator_tokens: dict
    orchestrator_cost_usd: float
    session_id: str | None = None
    platform_list_cost_usd: float | None = None  # managed mode: the session's authoritative list cost


class DryRunBackend:
    name = "dry-run"

    def execute(self, ctx: RunContext) -> ExecResult:
        from .dry_run import DryRunWorkers

        workers = DryRunWorkers(ctx.registry, ctx.run_id, ctx.inject)
        orch = SequentialOrchestrator(ctx.registry, ctx.pipeline, workers, ctx.run_dir, ctx.budget_usd)
        result = orch.run(ctx.run_id, ctx.input_path, ctx.started_at)

        # The orchestrator's own turns: read the pipeline, brief each worker, validate each report.
        rng = random.Random(ctx.run_id + ":orchestrator")
        n = len(result.steps)
        stable = len(ctx.registry.orchestrator.system) + len(ctx.registry.skill_text("deal-desk-orchestrator")) + 12000
        tokens = {
            "cache_write": stable // 4,
            "cache_read": (stable // 4) * (2 * n),
            "input": n * rng.randint(1800, 3200),
            "output": n * rng.randint(500, 900) + 400,
        }
        model = ctx.registry.orchestrator.model
        return ExecResult(result=result, orchestrator_tokens=tokens, orchestrator_cost_usd=cost_usd(model, tokens))


def get_backend(name: str):
    if name == "dry-run":
        return DryRunBackend()
    if name == "managed":
        from .managed import ManagedAgentsBackend

        return ManagedAgentsBackend.from_env()
    raise ValueError(f"unknown backend {name!r}")
