"""Benchmark isolation, honest verdicts, and aggregate accounting."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from codesm.eval.task import EvalTask, load_task
from codesm.eval.runner import run_task
from codesm.eval.compare import ComparisonResult, run_comparison
from codesm.eval.metrics import EvalReport, AssertionResult
from codesm.provider.base import StreamChunk
from codesm.config import Config


@pytest.mark.asyncio
async def test_comparisons_reset_fixture_and_count_children(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "target").write_text("before")
    seen = []
    closed = []

    class FakeAgent:
        def __init__(self, directory, config, **kwargs):
            self.directory = directory
            self.config = config
            self.session = SimpleNamespace(id="eval-test")
        async def chat(self, prompt):
            assert (self.directory / "target").read_text() == "before"
            seen.append((self.config.delegation, self.directory))
            (self.directory / "target").write_text("after")
            self._eval_events.extend([
                {"type": "tool_result", "tool": "write", "run_id": "child"},
                {"type": "subagent_done", "status": "completed", "run_id": "child"},
                {"type": "usage", "cost": .02, "cost_known": True, "estimated": False},
            ])
            self._eval_usage.update(tokens_in=25, tokens_out=5)
            yield StreamChunk(type="text", content="done")
            yield StreamChunk(type="run_status", content="completed")
        async def cleanup(self):
            closed.append(self.directory)
    monkeypatch.setattr("codesm.agent.agent.Agent", FakeAgent)
    task = EvalTask("isolation", "", "fix", assertion=["test $(cat target) = after"])
    result = await run_comparison(task, models=["openai/fixture"], directory_override=fixture,
        variants=["single", "specialists", "adaptive"], repeat=2, config=Config())
    assert result.all_passed and len(result.runs) == 6
    assert (fixture / "target").read_text() == "before"
    assert len(set(path for _, path in seen)) == 6
    assert all(not path.exists() for path in closed)
    assert result.runs[0].tool_calls == {"write": 1}
    assert result.runs[0].tokens_in == 25 and result.runs[0].subagents == 1
    assert all(group["cost_per_success_usd"] == .02 for group in result.summarize())
    protected_task = EvalTask("protected", "", "fix", assertion=["true"], protected_files=["target"])
    report = await run_task(protected_task, directory_override=fixture, config=Config())
    assert not report.passed and report.assertions[0].command == "Preserve target"


def test_failure_cost_is_included_and_missing_prices_stay_unknown():
    passed = EvalReport(model="m", variant="single", cost_usd=.1, assertions=[AssertionResult("check", 0)])
    failed = EvalReport(model="m", variant="single", cost_usd=.2, assertions=[AssertionResult("check", 1)])
    comparison = ComparisonResult("cost", "", "", [passed, failed])
    stats = comparison.summarize()[0]
    assert stats["pass_rate"] == .5 and stats["cost_per_success_usd"] == pytest.approx(.3)
    failed.cost_usd = None
    assert comparison.summarize()[0]["cost_per_success_usd"] is None
    assert not EvalReport().passed
    passed.max_iterations_hit = True
    assert not passed.passed


def test_benchmark_validation_and_corpus():
    for path in Path("benchmarks").glob("*.yaml"):
        task = load_task(path)
        assert task.assertion and not any("/tmp/codesm" in cmd for cmd in task.setup)
