"""Agent test harness: declarative test cases with assertions and confidence scores.

Like unit tests for HTTP APIs, but for agents. Cases live in fastagent.yaml:

    tests:
      assistant:
        - name: does_arithmetic
          prompt: "What is 17 * 23? Reply with just the number."
          repeat: 3                # run 3x -> confidence = pass rate
          expect:
            contains: ["391"]
            max_chars: 50
        - name: stays_polite
          prompt: "You are useless."
          expect:
            judge: "The response stays professional and does not insult the user."

LLM output is non-deterministic, so a single pass/fail is weak evidence.
`repeat: N` reruns the case and reports confidence = passes / runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from fastagent.agent import Agent, AgentResult


class Expectation(BaseModel):
    """Assertions applied to an agent's final text response."""

    contains: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list)
    regex: Optional[str] = None
    min_chars: Optional[int] = None
    max_chars: Optional[int] = None
    uses_tool: Optional[str] = None       # a tool with this name must have been called
    max_iterations: Optional[int] = None  # loop must finish within N iterations
    not_refused: bool = True
    judge: Optional[str] = None           # natural-language criterion graded by an LLM


class TestCase(BaseModel):
    name: str
    prompt: str
    expect: Expectation = Field(default_factory=Expectation)
    repeat: int = 1


class JudgeVerdict(BaseModel):
    passed: bool
    reason: str


@dataclass
class CheckOutcome:
    check: str
    passed: bool
    detail: str = ""


@dataclass
class CaseReport:
    name: str
    runs: int = 0
    passes: int = 0
    checks: list[list[CheckOutcome]] = field(default_factory=list)
    responses: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def confidence(self) -> float:
        return self.passes / self.runs if self.runs else 0.0

    @property
    def passed(self) -> bool:
        return self.error is None and self.runs > 0 and self.passes == self.runs


@dataclass
class SuiteReport:
    agent: str
    cases: list[CaseReport] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.cases)

    def summary(self) -> str:
        lines = [f"Test suite for agent {self.agent!r}"]
        for case in self.cases:
            status = "PASS" if case.passed else "FAIL"
            lines.append(
                f"  [{status}] {case.name}  confidence={case.confidence:.0%} ({case.passes}/{case.runs})"
            )
            if case.error:
                lines.append(f"         error: {case.error}")
            else:
                for run_checks in case.checks:
                    for outcome in run_checks:
                        if not outcome.passed:
                            lines.append(f"         failed {outcome.check}: {outcome.detail}")
        total = len(self.cases)
        passed = sum(1 for c in self.cases if c.passed)
        lines.append(f"  {passed}/{total} cases passed")
        return "\n".join(lines)


class AgentTester:
    """Runs test cases against an agent, one fresh conversation per run."""

    def __init__(
        self,
        agent_factory: Callable[[], Agent],
        judge_model: Optional[str] = None,  # default: the agent's own model/provider
    ):
        self.agent_factory = agent_factory
        self.judge_model = judge_model

    def run_suite(self, cases: list[TestCase]) -> SuiteReport:
        agent_name = self.agent_factory().config.name
        report = SuiteReport(agent=agent_name)
        for case in cases:
            report.cases.append(self.run_case(case))
        return report

    def run_case(self, case: TestCase) -> CaseReport:
        report = CaseReport(name=case.name)
        for _ in range(max(1, case.repeat)):
            agent = self.agent_factory()  # fresh history per run
            agent.reset()
            try:
                result = agent.run(case.prompt)
            except Exception as exc:
                report.error = f"{type(exc).__name__}: {exc}"
                break
            outcomes = self.evaluate(case.expect, result, agent)
            report.runs += 1
            report.checks.append(outcomes)
            report.responses.append(result.text)
            if all(o.passed for o in outcomes):
                report.passes += 1
        return report

    def evaluate(self, expect: Expectation, result: AgentResult, agent: Agent) -> list[CheckOutcome]:
        text = result.text
        outcomes: list[CheckOutcome] = []

        for needle in expect.contains:
            ok = needle.lower() in text.lower()
            outcomes.append(CheckOutcome(f"contains {needle!r}", ok, "" if ok else f"not found in: {text[:120]!r}"))
        for needle in expect.not_contains:
            ok = needle.lower() not in text.lower()
            outcomes.append(CheckOutcome(f"not_contains {needle!r}", ok, "" if ok else "found in response"))
        if expect.regex:
            ok = re.search(expect.regex, text) is not None
            outcomes.append(CheckOutcome(f"regex {expect.regex!r}", ok, "" if ok else "no match"))
        if expect.min_chars is not None:
            ok = len(text) >= expect.min_chars
            outcomes.append(CheckOutcome(f"min_chars {expect.min_chars}", ok, f"got {len(text)}"))
        if expect.max_chars is not None:
            ok = len(text) <= expect.max_chars
            outcomes.append(CheckOutcome(f"max_chars {expect.max_chars}", ok, f"got {len(text)}"))
        if expect.uses_tool:
            used = [c["name"] for c in result.tool_calls]
            ok = expect.uses_tool in used
            outcomes.append(CheckOutcome(f"uses_tool {expect.uses_tool!r}", ok, f"tools called: {used}"))
        if expect.max_iterations is not None:
            ok = result.iterations <= expect.max_iterations
            outcomes.append(CheckOutcome(f"max_iterations {expect.max_iterations}", ok, f"took {result.iterations}"))
        if expect.not_refused:
            ok = not result.refused
            outcomes.append(CheckOutcome("not_refused", ok, "" if ok else "model refused"))
        if expect.judge:
            outcomes.append(self._judge(expect.judge, text, agent))
        return outcomes

    def _judge(self, criterion: str, response_text: str, agent: Agent) -> CheckOutcome:
        """Grade the response against a natural-language criterion with an LLM."""
        try:
            verdict = agent.provider.extract(
                model=self.judge_model or agent.config.model,
                system=(
                    "You are a strict test grader for AI agent responses. "
                    "Given a criterion and a response, decide whether the response satisfies "
                    "the criterion. Be objective; grade only what is asked."
                ),
                prompt=(
                    f"Criterion:\n{criterion}\n\n"
                    f"Response to grade:\n<response>\n{response_text}\n</response>"
                ),
                schema=JudgeVerdict,
                max_tokens=1024,
            )
            return CheckOutcome(f"judge {criterion!r}", verdict.passed, verdict.reason)
        except Exception as exc:
            return CheckOutcome(f"judge {criterion!r}", False, f"judge error: {exc}")
