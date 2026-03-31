#!/usr/bin/env python3
"""
Cache Evaluation Script for Claude Code CLI
=============================================
Measures prompt cache hit rates across multiple trials and CLI versions.
Parses session JSONL logs from ~/.claude/projects/ to extract token metrics.

Usage:
    python cache_eval.py --phase baseline --trials 10
    python cache_eval.py --phase optimized --trials 10 --cli /path/to/patched/claude
    python cache_eval.py --phase compare
    python cache_eval.py --phase all --trials 10
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
import uuid as _uuid
from typing import Optional
from collections import defaultdict

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    import numpy as np
    from scipy import stats as sp_stats
except ImportError:
    print("Install dependencies: pip install matplotlib numpy scipy")
    sys.exit(1)

EXPERIMENTS_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = EXPERIMENTS_DIR / "prompts"
RESULTS_DIR = EXPERIMENTS_DIR / "results"
FIGURES_DIR = EXPERIMENTS_DIR / "figures"
BASELINE_DIR = RESULTS_DIR / "baseline"
OPTIMIZED_DIR = RESULTS_DIR / "optimized"

CLAUDE_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
PROJECTS_DIR = CLAUDE_CONFIG_DIR / "projects"

COLORS = {
    "baseline": "#E74C3C",
    "optimized": "#27AE60",
    "ci_alpha": 0.15,
    "grid": "#E0E0E0",
    "text": "#333333",
    "background": "#FFFFFF",
}

MODEL_PRICING = {
    "claude-sonnet-4-20250514": {
        "input": 3.0 / 1_000_000,
        "cache_write": 3.75 / 1_000_000,
        "cache_read": 0.30 / 1_000_000,
        "output": 15.0 / 1_000_000,
    },
    "claude-3-5-sonnet-20241022": {
        "input": 3.0 / 1_000_000,
        "cache_write": 3.75 / 1_000_000,
        "cache_read": 0.30 / 1_000_000,
        "output": 15.0 / 1_000_000,
    },
    "default": {
        "input": 3.0 / 1_000_000,
        "cache_write": 3.75 / 1_000_000,
        "cache_read": 0.30 / 1_000_000,
        "output": 15.0 / 1_000_000,
    },
}


@dataclass
class TurnMetrics:
    turn: int
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    timestamp: str = ""
    request_id: str = ""
    session_id: str = ""

    @property
    def total_input_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    @property
    def cache_hit_rate(self) -> float:
        total = self.total_input_tokens
        return (self.cache_read_input_tokens / total * 100) if total > 0 else 0.0

    @property
    def cache_miss_rate(self) -> float:
        return 100.0 - self.cache_hit_rate

    @property
    def cost(self) -> float:
        pricing = MODEL_PRICING.get(self.model, MODEL_PRICING["default"])
        return (
            self.input_tokens * pricing["input"]
            + self.cache_creation_input_tokens * pricing["cache_write"]
            + self.cache_read_input_tokens * pricing["cache_read"]
            + self.output_tokens * pricing["output"]
        )

    @property
    def savings_from_cache(self) -> float:
        pricing = MODEL_PRICING.get(self.model, MODEL_PRICING["default"])
        return self.cache_read_input_tokens * (pricing["input"] - pricing["cache_read"])


@dataclass
class TrialResult:
    trial_id: int
    phase: str
    timestamp: str
    turns: list[TurnMetrics] = field(default_factory=list)
    session_file: str = ""
    error: str = ""

    @property
    def total_input(self) -> int:
        return sum(t.total_input_tokens for t in self.turns)

    @property
    def total_cache_read(self) -> int:
        return sum(t.cache_read_input_tokens for t in self.turns)

    @property
    def total_cache_creation(self) -> int:
        return sum(t.cache_creation_input_tokens for t in self.turns)

    @property
    def total_output(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    @property
    def overall_cache_hit_rate(self) -> float:
        total = self.total_input
        return (self.total_cache_read / total * 100) if total > 0 else 0.0

    @property
    def total_cost(self) -> float:
        return sum(t.cost for t in self.turns)

    @property
    def total_savings(self) -> float:
        return sum(t.savings_from_cache for t in self.turns)

    @property
    def turn_2_plus_cache_hit_rate(self) -> float:
        t2 = [t for t in self.turns if t.turn >= 2]
        if not t2:
            return 0.0
        total = sum(t.total_input_tokens for t in t2)
        return (
            (sum(t.cache_read_input_tokens for t in t2) / total * 100)
            if total > 0
            else 0.0
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["turns"] = [asdict(t) for t in self.turns]
        d["summary"] = {
            "total_input": self.total_input,
            "total_cache_read": self.total_cache_read,
            "total_cache_creation": self.total_cache_creation,
            "total_output": self.total_output,
            "overall_cache_hit_rate": round(self.overall_cache_hit_rate, 2),
            "turn_2_plus_cache_hit_rate": round(self.turn_2_plus_cache_hit_rate, 2),
            "total_cost": round(self.total_cost, 6),
            "total_savings": round(self.total_savings, 6),
            "num_turns": len(self.turns),
        }
        return d


@dataclass
class SummaryStats:
    phase: str
    num_trials: int
    mean_cache_hit_rate: float
    std_cache_hit_rate: float
    ci_95_cache_hit_rate: tuple[float, float]
    mean_turn_2_plus_hit_rate: float
    std_turn_2_plus_hit_rate: float
    mean_total_cost: float
    mean_total_input: float
    mean_total_cache_read: float
    per_turn_mean: list[float] = field(default_factory=list)
    per_turn_std: list[float] = field(default_factory=list)
    per_turn_ci: list[tuple[float, float]] = field(default_factory=list)


class LogParser:
    @staticmethod
    def find_session_file(session_id: str) -> Optional[Path]:
        if not PROJECTS_DIR.exists():
            return None
        for jsonl in PROJECTS_DIR.rglob("*.jsonl"):
            if jsonl.stem == session_id:
                return jsonl
            if "subagents" in jsonl.parts:
                continue
        return None

    @staticmethod
    def find_latest_session(after: datetime) -> Optional[Path]:
        latest: Optional[Path] = None
        latest_time = 0.0
        if not PROJECTS_DIR.exists():
            return None
        for jsonl in PROJECTS_DIR.rglob("*.jsonl"):
            if "subagents" in jsonl.parts:
                continue
            try:
                mtime = jsonl.stat().st_mtime
                mt = datetime.fromtimestamp(mtime, tz=timezone.utc)
                if mt > after and mtime > latest_time:
                    latest = jsonl
                    latest_time = mtime
            except OSError:
                continue
        return latest

    @staticmethod
    def find_all_sessions(after: datetime) -> list[Path]:
        if not PROJECTS_DIR.exists():
            return []
        found: list[tuple[float, Path]] = []
        for jsonl in PROJECTS_DIR.rglob("*.jsonl"):
            if "subagents" in jsonl.parts:
                continue
            try:
                mtime = jsonl.stat().st_mtime
                mt = datetime.fromtimestamp(mtime, tz=timezone.utc)
                if mt > after:
                    found.append((mtime, jsonl))
            except OSError:
                continue
        found.sort(key=lambda x: x[0])
        return [p for _, p in found]

    @staticmethod
    def parse_session(jsonl_path: Path) -> list[TurnMetrics]:
        turns: list[TurnMetrics] = []
        turn_number = 0

        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") != "assistant":
                    continue

                msg = entry.get("message", {})
                model = msg.get("model", "")

                if model == "<synthetic>":
                    continue

                usage = msg.get("usage", {})

                is_sidechain = entry.get("isSidechain", False)
                if is_sidechain:
                    continue

                output_tokens = usage.get("output_tokens", 0)
                if output_tokens == 0:
                    continue

                turn_number += 1
                turns.append(
                    TurnMetrics(
                        turn=turn_number,
                        model=model,
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        cache_creation_input_tokens=usage.get(
                            "cache_creation_input_tokens", 0
                        ),
                        cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
                        timestamp=entry.get("timestamp", ""),
                        request_id=entry.get("requestId", ""),
                        session_id=entry.get("sessionId", ""),
                    )
                )

        return turns


class TrialRunner:
    def __init__(
        self,
        cli_binary: str = "claude",
        model: str = "claude-sonnet-4-20250514",
        max_turns: int = 10,
        working_dir: Optional[str] = None,
    ):
        self.cli_binary = cli_binary
        self.model = model
        self.max_turns = max_turns
        self.working_dir = working_dir or os.getcwd()

    def load_prompts(self, path: Optional[Path] = None) -> list[str]:
        if path is None:
            path = PROMPTS_DIR / "standard_10turn.jsonl"
        prompts = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                prompts.append(obj["content"])
        return prompts[: self.max_turns]

    def _build_cmd(self, extra_args: list[str]) -> list[str]:
        if self.cli_binary.startswith("node "):
            return self.cli_binary.split() + extra_args
        return [self.cli_binary] + extra_args

    def run_multi_turn_session(self, prompts: list[str]) -> TrialResult:
        before = datetime.now(tz=timezone.utc)
        errors: list[str] = []
        env = os.environ.copy()
        env["CLAUDE_CODE_DISABLE_MCP"] = "1"

        session_id = str(_uuid.uuid4())

        for i, prompt in enumerate(prompts):
            try:
                if i == 0:
                    args = [
                        "-p",
                        prompt,
                        "--model",
                        self.model,
                        "--output-format",
                        "text",
                        "--verbose",
                        "--dangerously-skip-permissions",
                        "--session-id",
                        session_id,
                    ]
                else:
                    args = [
                        "-p",
                        prompt,
                        "--resume",
                        session_id,
                        "--model",
                        self.model,
                        "--output-format",
                        "text",
                        "--verbose",
                        "--dangerously-skip-permissions",
                    ]

                full_cmd = self._build_cmd(args)
                result = subprocess.run(
                    full_cmd,
                    capture_output=True,
                    text=True,
                    cwd=self.working_dir,
                    env=env,
                    timeout=300,
                )

                if result.returncode != 0:
                    errors.append(f"Turn {i + 1} failed: {result.stderr[:300]}")

                time.sleep(1)

            except subprocess.TimeoutExpired:
                errors.append(f"Turn {i + 1} timed out after 300s")
            except Exception as e:
                errors.append(f"Turn {i + 1} error: {e}")

        time.sleep(2)
        session_file = LogParser.find_session_file(session_id)
        if session_file is None:
            session_file = LogParser.find_latest_session(after=before)
        if session_file is None:
            return TrialResult(
                trial_id=0,
                phase="",
                timestamp=before.isoformat(),
                error=f"No session file found for {session_id}. Errors: {'; '.join(errors)}",
            )

        turns = LogParser.parse_session(session_file)

        return TrialResult(
            trial_id=0,
            phase="",
            timestamp=before.isoformat(),
            turns=turns,
            session_file=str(session_file),
            error="; ".join(errors) if errors else "",
        )

    def run_trial(self, trial_id: int, phase: str) -> TrialResult:
        prompts = self.load_prompts()
        result = self.run_multi_turn_session(prompts)
        result.trial_id = trial_id
        result.phase = phase
        return result


class Statistics:
    @staticmethod
    def compute_summary(results: list[TrialResult]) -> SummaryStats:
        if not results:
            raise ValueError("No results to summarize")

        overall_rates = [r.overall_cache_hit_rate for r in results]
        t2_rates = [r.turn_2_plus_cache_hit_rate for r in results]
        costs = [r.total_cost for r in results]
        total_inputs = [r.total_input for r in results]
        total_cache_reads = [r.total_cache_read for r in results]

        n = len(results)
        mean_hr = np.mean(overall_rates)
        std_hr = np.std(overall_rates, ddof=1) if n > 1 else 0.0
        se_hr = std_hr / np.sqrt(n)
        ci_95 = (mean_hr - 1.96 * se_hr, mean_hr + 1.96 * se_hr)

        max_turns = max(len(r.turns) for r in results)
        per_turn_mean = []
        per_turn_std = []
        per_turn_ci = []
        for t in range(1, max_turns + 1):
            rates = []
            for r in results:
                for turn in r.turns:
                    if turn.turn == t:
                        rates.append(turn.cache_hit_rate)
                        break
            if rates:
                m = np.mean(rates)
                s = np.std(rates, ddof=1) if len(rates) > 1 else 0.0
                se = s / np.sqrt(len(rates))
                per_turn_mean.append(m)
                per_turn_std.append(s)
                per_turn_ci.append((m - 1.96 * se, m + 1.96 * se))
            else:
                per_turn_mean.append(0.0)
                per_turn_std.append(0.0)
                per_turn_ci.append((0.0, 0.0))

        return SummaryStats(
            phase=results[0].phase,
            num_trials=n,
            mean_cache_hit_rate=float(mean_hr),
            std_cache_hit_rate=float(std_hr),
            ci_95_cache_hit_rate=(float(ci_95[0]), float(ci_95[1])),
            mean_turn_2_plus_hit_rate=float(np.mean(t2_rates)),
            std_turn_2_plus_hit_rate=float(np.std(t2_rates, ddof=1)) if n > 1 else 0.0,
            mean_total_cost=float(np.mean(costs)),
            mean_total_input=float(np.mean(total_inputs)),
            mean_total_cache_read=float(np.mean(total_cache_reads)),
            per_turn_mean=per_turn_mean,
            per_turn_std=per_turn_std,
            per_turn_ci=per_turn_ci,
        )

    @staticmethod
    def paired_comparison(
        baseline: list[TrialResult], optimized: list[TrialResult]
    ) -> dict:
        min_trials = min(len(baseline), len(optimized))
        b_rates = [baseline[i].overall_cache_hit_rate for i in range(min_trials)]
        o_rates = [optimized[i].overall_cache_hit_rate for i in range(min_trials)]

        b_t2 = [baseline[i].turn_2_plus_cache_hit_rate for i in range(min_trials)]
        o_t2 = [optimized[i].turn_2_plus_cache_hit_rate for i in range(min_trials)]

        b_costs = [baseline[i].total_cost for i in range(min_trials)]
        o_costs = [optimized[i].total_cost for i in range(min_trials)]

        def cohens_d(x, y):
            diff = np.array(y) - np.array(x)
            return float(np.mean(diff) / (np.std(diff, ddof=1) + 1e-10))

        def paired_t(x, y):
            if len(x) < 2:
                return 1.0, 0.0
            t_stat, p_val = sp_stats.ttest_rel(y, x)
            return float(p_val), float(t_stat)

        p_overall, t_overall = paired_t(b_rates, o_rates)
        p_t2, t_t2 = paired_t(b_t2, o_t2)
        p_cost, t_cost = paired_t(b_costs, o_costs)

        return {
            "overall": {
                "baseline_mean": float(np.mean(b_rates)),
                "optimized_mean": float(np.mean(o_rates)),
                "improvement_pp": float(np.mean(o_rates) - np.mean(b_rates)),
                "p_value": p_overall,
                "t_statistic": t_overall,
                "cohens_d": cohens_d(b_rates, o_rates),
                "significant_005": p_overall < 0.05,
            },
            "turn_2_plus": {
                "baseline_mean": float(np.mean(b_t2)),
                "optimized_mean": float(np.mean(o_t2)),
                "improvement_pp": float(np.mean(o_t2) - np.mean(b_t2)),
                "p_value": p_t2,
                "t_statistic": t_t2,
                "cohens_d": cohens_d(b_t2, o_t2),
                "significant_005": p_t2 < 0.05,
            },
            "cost": {
                "baseline_mean": float(np.mean(b_costs)),
                "optimized_mean": float(np.mean(o_costs)),
                "savings": float(np.mean(b_costs) - np.mean(o_costs)),
                "p_value": p_cost,
                "t_statistic": t_cost,
                "cohens_d": cohens_d(b_costs, o_costs),
            },
            "min_trials": min_trials,
        }

    @staticmethod
    def format_comparison_table(comp: dict) -> str:
        lines = []
        lines.append("=" * 72)
        lines.append("STATISTICAL COMPARISON: Baseline vs Optimized")
        lines.append("=" * 72)
        lines.append("")

        for label, key in [
            ("Overall Cache Hit Rate", "overall"),
            ("Turn 2+ Cache Hit Rate", "turn_2_plus"),
            ("Cost per Session ($)", "cost"),
        ]:
            d = comp[key]
            lines.append(f"  {label}")
            lines.append(
                f"    Baseline mean:    {d['baseline_mean']:.2f}"
                + ("%" if key != "cost" else "")
            )
            lines.append(
                f"    Optimized mean:   {d['optimized_mean']:.2f}"
                + ("%" if key != "cost" else "")
            )
            imp = d.get("improvement_pp", d.get("savings", 0))
            unit = "pp" if key != "cost" else "$"
            lines.append(f"    Improvement:      {imp:+.2f} {unit}")
            lines.append(f"    p-value:          {d['p_value']:.4f}")
            lines.append(f"    t-statistic:      {d['t_statistic']:.4f}")
            lines.append(f"    Cohen's d:        {d['cohens_d']:.4f}")
            sig = d.get("significant_005")
            if sig is not None:
                lines.append(f"    Significant (α=0.05): {'YES' if sig else 'NO'}")
            lines.append("")

        lines.append(f"  Paired trials: {comp['min_trials']}")
        return "\n".join(lines)


class Visualization:
    @staticmethod
    def setup_style():
        matplotlib.rcParams.update(
            {
                "font.family": "sans-serif",
                "font.size": 11,
                "axes.titlesize": 13,
                "axes.labelsize": 11,
                "figure.dpi": 150,
                "savefig.dpi": 300,
                "savefig.bbox": "tight",
                "axes.grid": True,
                "grid.alpha": 0.3,
                "grid.color": COLORS["grid"],
            }
        )

    @staticmethod
    def plot_cache_hit_rate_comparison(
        baseline_stats: SummaryStats,
        optimized_stats: SummaryStats,
        output_path: Path,
    ):
        Visualization.setup_style()
        fig, ax = plt.subplots(figsize=(10, 6))

        max_turns = max(
            len(baseline_stats.per_turn_mean),
            len(optimized_stats.per_turn_mean),
        )
        turns = list(range(1, max_turns + 1))

        b_mean = baseline_stats.per_turn_mean + [0.0] * (
            max_turns - len(baseline_stats.per_turn_mean)
        )
        o_mean = optimized_stats.per_turn_mean + [0.0] * (
            max_turns - len(optimized_stats.per_turn_mean)
        )
        b_ci = baseline_stats.per_turn_ci + [(0, 0)] * (
            max_turns - len(baseline_stats.per_turn_ci)
        )
        o_ci = optimized_stats.per_turn_ci + [(0, 0)] * (
            max_turns - len(optimized_stats.per_turn_ci)
        )

        b_lower = [c[0] for c in b_ci]
        b_upper = [c[1] for c in b_ci]
        o_lower = [c[0] for c in o_ci]
        o_upper = [c[1] for c in o_ci]

        ax.plot(
            turns,
            b_mean,
            "o-",
            color=COLORS["baseline"],
            label="Baseline",
            linewidth=2,
            markersize=6,
        )
        ax.fill_between(
            turns, b_lower, b_upper, color=COLORS["baseline"], alpha=COLORS["ci_alpha"]
        )
        ax.plot(
            turns,
            o_mean,
            "s-",
            color=COLORS["optimized"],
            label="Optimized",
            linewidth=2,
            markersize=6,
        )
        ax.fill_between(
            turns, o_lower, o_upper, color=COLORS["optimized"], alpha=COLORS["ci_alpha"]
        )

        ax.set_xlabel("Conversation Turn")
        ax.set_ylabel("Cache Hit Rate (%)")
        ax.set_title("Prompt Cache Hit Rate by Conversation Turn")
        ax.set_xticks(turns)
        ax.set_ylim(-5, 105)
        ax.legend(loc="lower right")
        ax.yaxis.set_major_formatter(mticker.PercentFormatter())

        fig.tight_layout()
        fig.savefig(output_path, dpi=300)
        plt.close(fig)
        print(f"  Saved: {output_path}")

    @staticmethod
    def plot_token_waste_per_turn(
        baseline_results: list[TrialResult],
        optimized_results: list[TrialResult],
        output_path: Path,
    ):
        Visualization.setup_style()
        fig, ax = plt.subplots(figsize=(10, 6))

        def get_per_turn_input(results: list[TrialResult]) -> dict[int, list[int]]:
            d: dict[int, list[int]] = defaultdict(list)
            for r in results:
                for t in r.turns:
                    d[t.turn].append(t.input_tokens)
            return d

        b_data = get_per_turn_input(baseline_results)
        o_data = get_per_turn_input(optimized_results)
        all_turns = sorted(set(b_data.keys()) | set(o_data.keys()))

        x = np.arange(len(all_turns))
        width = 0.35

        b_means = [np.mean(b_data.get(t, [0])) for t in all_turns]
        b_stds = [
            np.std(b_data.get(t, [0]), ddof=1) if len(b_data.get(t, [0])) > 1 else 0
            for t in all_turns
        ]
        o_means = [np.mean(o_data.get(t, [0])) for t in all_turns]
        o_stds = [
            np.std(o_data.get(t, [0]), ddof=1) if len(o_data.get(t, [0])) > 1 else 0
            for t in all_turns
        ]

        ax.bar(
            x - width / 2,
            b_means,
            width,
            yerr=b_stds,
            label="Baseline",
            color=COLORS["baseline"],
            alpha=0.8,
            capsize=3,
        )
        ax.bar(
            x + width / 2,
            o_means,
            width,
            yerr=o_stds,
            label="Optimized",
            color=COLORS["optimized"],
            alpha=0.8,
            capsize=3,
        )

        ax.set_xlabel("Conversation Turn")
        ax.set_ylabel("Cache Miss Tokens (input_tokens)")
        ax.set_title("Cache Miss Tokens per Turn (Lower is Better)")
        ax.set_xticks(x)
        ax.set_xticklabels([str(t) for t in all_turns])
        ax.legend()
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))

        fig.tight_layout()
        fig.savefig(output_path, dpi=300)
        plt.close(fig)
        print(f"  Saved: {output_path}")

    @staticmethod
    def plot_cumulative_savings(
        baseline_results: list[TrialResult],
        optimized_results: list[TrialResult],
        output_path: Path,
    ):
        Visualization.setup_style()
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

        def get_cumulative_tokens(
            results: list[TrialResult], field: str
        ) -> list[tuple[list[int], list[int]]]:
            series = []
            for r in results:
                turns_x = []
                cum = []
                running = 0
                for t in r.turns:
                    running += getattr(t, field)
                    turns_x.append(t.turn)
                    cum.append(running)
                series.append((turns_x, cum))
            return series

        b_series_input = get_cumulative_tokens(baseline_results, "input_tokens")
        o_series_input = get_cumulative_tokens(optimized_results, "input_tokens")

        for sx, sy in b_series_input:
            ax1.plot(sx, sy, color=COLORS["baseline"], alpha=0.3, linewidth=1)
        for sx, sy in o_series_input:
            ax1.plot(sx, sy, color=COLORS["optimized"], alpha=0.3, linewidth=1)

        max_turns = max(
            max((len(s[0]) for s in b_series_input), default=0),
            max((len(s[0]) for s in o_series_input), default=0),
        )
        turns_range = list(range(1, max_turns + 1))

        def interpolate_mean(series_list, max_t):
            all_y = defaultdict(list)
            for sx, sy in series_list:
                for t_idx, t in enumerate(sx):
                    all_y[t].append(sy[t_idx])
            means = [np.mean(all_y.get(t, [0])) for t in range(1, max_t + 1)]
            return means

        b_mean_input = interpolate_mean(b_series_input, max_turns)
        o_mean_input = interpolate_mean(o_series_input, max_turns)
        ax1.plot(
            turns_range,
            b_mean_input,
            color=COLORS["baseline"],
            linewidth=2.5,
            label="Baseline",
        )
        ax1.plot(
            turns_range,
            o_mean_input,
            color=COLORS["optimized"],
            linewidth=2.5,
            label="Optimized",
        )
        ax1.set_xlabel("Conversation Turn")
        ax1.set_ylabel("Cumulative Cache Miss Tokens")
        ax1.set_title("Cumulative Cache Miss Tokens")
        ax1.legend()
        ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))

        savings = [b - o for b, o in zip(b_mean_input, o_mean_input)]
        ax2.fill_between(turns_range, savings, color=COLORS["optimized"], alpha=0.3)
        ax2.plot(turns_range, savings, color=COLORS["optimized"], linewidth=2.5)
        if savings:
            total_saved = savings[-1]
            ax2.annotate(
                f"Total saved: {total_saved:,.0f} tokens",
                xy=(turns_range[-1], total_saved),
                xytext=(turns_range[-1] - 3, total_saved * 0.5),
                fontsize=10,
                arrowprops=dict(arrowstyle="->", color=COLORS["text"]),
                color=COLORS["text"],
            )
        ax2.set_xlabel("Conversation Turn")
        ax2.set_ylabel("Tokens Saved")
        ax2.set_title("Cumulative Savings (Baseline - Optimized)")
        ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))

        fig.tight_layout()
        fig.savefig(output_path, dpi=300)
        plt.close(fig)
        print(f"  Saved: {output_path}")

    @staticmethod
    def plot_statistical_significance(
        baseline_results: list[TrialResult],
        optimized_results: list[TrialResult],
        comparison: dict,
        output_path: Path,
    ):
        Visualization.setup_style()
        fig, axes = plt.subplots(1, 3, figsize=(16, 5))

        metrics = [
            (
                "Overall Cache Hit Rate (%)",
                "overall",
                "baseline_mean",
                "optimized_mean",
            ),
            ("Turn 2+ Hit Rate (%)", "turn_2_plus", "baseline_mean", "optimized_mean"),
            ("Cost ($)", "cost", "baseline_mean", "optimized_mean"),
        ]

        min_trials = min(len(baseline_results), len(optimized_results))

        for ax, (title, key, _, _) in zip(axes, metrics):
            if key == "overall":
                b_vals = [
                    r.overall_cache_hit_rate for r in baseline_results[:min_trials]
                ]
                o_vals = [
                    r.overall_cache_hit_rate for r in optimized_results[:min_trials]
                ]
            elif key == "turn_2_plus":
                b_vals = [
                    r.turn_2_plus_cache_hit_rate for r in baseline_results[:min_trials]
                ]
                o_vals = [
                    r.turn_2_plus_cache_hit_rate for r in optimized_results[:min_trials]
                ]
            else:
                b_vals = [r.total_cost for r in baseline_results[:min_trials]]
                o_vals = [r.total_cost for r in optimized_results[:min_trials]]

            bp = ax.boxplot(
                [b_vals, o_vals],
                tick_labels=["Baseline", "Optimized"],
                patch_artist=True,
                widths=0.5,
            )
            bp["boxes"][0].set_facecolor(COLORS["baseline"])
            bp["boxes"][0].set_alpha(0.6)
            bp["boxes"][1].set_facecolor(COLORS["optimized"])
            bp["boxes"][1].set_alpha(0.6)

            for i, vals in enumerate([b_vals, o_vals]):
                jitter = np.random.default_rng(42).uniform(-0.05, 0.05, len(vals))
                ax.scatter(
                    [i + 1 + j for j in jitter],
                    vals,
                    alpha=0.5,
                    color=COLORS["baseline"] if i == 0 else COLORS["optimized"],
                    zorder=3,
                    s=30,
                )

            d = comparison[key]
            p_val = d["p_value"]
            sig_marker = (
                "***"
                if p_val < 0.001
                else "**"
                if p_val < 0.01
                else "*"
                if p_val < 0.05
                else "n.s."
            )
            ax.set_title(f"{title}\np={p_val:.4f} {sig_marker}")

            y_max = max(max(b_vals), max(o_vals))
            y_min = min(min(b_vals), min(o_vals))
            y_range = y_max - y_min if y_max != y_min else 1
            ax.annotate(
                f"p = {p_val:.4f}\nCohen's d = {d['cohens_d']:.2f}",
                xy=(0.5, 0.95),
                xycoords="axes fraction",
                ha="center",
                va="top",
                fontsize=9,
                bbox=dict(boxstyle="round,pad=0.3", facecolor="lightyellow", alpha=0.8),
            )

        fig.suptitle(
            "Statistical Significance: Baseline vs Optimized",
            fontsize=14,
            fontweight="bold",
        )
        fig.tight_layout()
        fig.savefig(output_path, dpi=300)
        plt.close(fig)
        print(f"  Saved: {output_path}")

    @staticmethod
    def plot_cache_heatmap(
        baseline_results: list[TrialResult],
        optimized_results: list[TrialResult],
        output_path: Path,
    ):
        Visualization.setup_style()
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

        def build_matrix(results: list[TrialResult]) -> np.ndarray:
            max_turns = max((len(r.turns) for r in results), default=0)
            n_trials = len(results)
            mat = np.full((n_trials, max_turns), np.nan)
            for i, r in enumerate(results):
                for t in r.turns:
                    if t.turn <= max_turns:
                        mat[i, t.turn - 1] = t.cache_hit_rate
            return mat

        b_mat = build_matrix(baseline_results)
        o_mat = build_matrix(optimized_results)

        vmin = 0
        vmax = 100
        cmap = plt.cm.RdYlGn

        im1 = ax1.imshow(b_mat, cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
        ax1.set_title("Baseline")
        ax1.set_xlabel("Turn")
        ax1.set_ylabel("Trial")
        ax1.set_xticks(range(b_mat.shape[1]))
        ax1.set_xticklabels(range(1, b_mat.shape[1] + 1))
        fig.colorbar(im1, ax=ax1, label="Cache Hit Rate (%)")

        im2 = ax2.imshow(o_mat, cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
        ax2.set_title("Optimized")
        ax2.set_xlabel("Turn")
        ax2.set_ylabel("Trial")
        ax2.set_xticks(range(o_mat.shape[1]))
        ax2.set_xticklabels(range(1, o_mat.shape[1] + 1))
        fig.colorbar(im2, ax=ax2, label="Cache Hit Rate (%)")

        fig.suptitle(
            "Cache Hit Rate Consistency Across Trials", fontsize=14, fontweight="bold"
        )
        fig.tight_layout()
        fig.savefig(output_path, dpi=300)
        plt.close(fig)
        print(f"  Saved: {output_path}")


def save_trial(result: TrialResult, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"trial_{result.trial_id:03d}.json"
    with open(path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    return path


def load_trials(directory: Path) -> list[TrialResult]:
    results = []
    if not directory.exists():
        return results
    for path in sorted(directory.glob("trial_*.json")):
        with open(path) as f:
            data = json.load(f)
        turns = [TurnMetrics(**t) for t in data["turns"]]
        results.append(
            TrialResult(
                trial_id=data["trial_id"],
                phase=data["phase"],
                timestamp=data["timestamp"],
                turns=turns,
                session_file=data.get("session_file", ""),
                error=data.get("error", ""),
            )
        )
    return results


def save_summary(stats: SummaryStats, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "summary.json"
    data = {
        "phase": stats.phase,
        "num_trials": stats.num_trials,
        "mean_cache_hit_rate": stats.mean_cache_hit_rate,
        "std_cache_hit_rate": stats.std_cache_hit_rate,
        "ci_95_cache_hit_rate": list(stats.ci_95_cache_hit_rate),
        "mean_turn_2_plus_hit_rate": stats.mean_turn_2_plus_hit_rate,
        "std_turn_2_plus_hit_rate": stats.std_turn_2_plus_hit_rate,
        "mean_total_cost": stats.mean_total_cost,
        "mean_total_input": stats.mean_total_input,
        "mean_total_cache_read": stats.mean_total_cache_read,
        "per_turn_mean": stats.per_turn_mean,
        "per_turn_std": stats.per_turn_std,
        "per_turn_ci": [list(c) for c in stats.per_turn_ci],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def print_summary_table(stats: SummaryStats):
    print(f"\n{'=' * 60}")
    print(f"  Summary: {stats.phase.upper()} ({stats.num_trials} trials)")
    print(f"{'=' * 60}")
    print(
        f"  Overall cache hit rate:  {stats.mean_cache_hit_rate:.1f}% ± {stats.std_cache_hit_rate:.1f}%"
    )
    print(
        f"  95% CI:                  [{stats.ci_95_cache_hit_rate[0]:.1f}%, {stats.ci_95_cache_hit_rate[1]:.1f}%]"
    )
    print(
        f"  Turn 2+ hit rate:        {stats.mean_turn_2_plus_hit_rate:.1f}% ± {stats.std_turn_2_plus_hit_rate:.1f}%"
    )
    print(f"  Mean total input tokens:  {stats.mean_total_input:,.0f}")
    print(f"  Mean cache read tokens:   {stats.mean_total_cache_read:,.0f}")
    print(f"  Mean cost per session:    ${stats.mean_total_cost:.4f}")
    print(f"\n  Per-turn cache hit rates:")
    for i, (m, s) in enumerate(zip(stats.per_turn_mean, stats.per_turn_std)):
        print(f"    Turn {i + 1:2d}: {m:6.1f}% ± {s:5.1f}%")
    print()


def run_phase(
    phase: str,
    trials: int,
    cli_binary: str,
    model: str,
    working_dir: Optional[str],
):
    output_dir = BASELINE_DIR if phase == "baseline" else OPTIMIZED_DIR

    existing = load_trials(output_dir)
    if len(existing) >= trials:
        print(f"  Found {len(existing)} existing {phase} trials, skipping execution.")
        print(f"  Delete {output_dir}/*.json to re-run.")
        return existing, Statistics.compute_summary(existing)

    runner = TrialRunner(
        cli_binary=cli_binary,
        model=model,
        working_dir=working_dir,
    )

    results: list[TrialResult] = list(existing)
    start_id = len(results) + 1
    for i in range(start_id, trials + 1):
        print(f"\n{'─' * 40}")
        print(f"  Trial {i}/{trials} [{phase}]")
        print(f"{'─' * 40}")

        result = runner.run_trial(trial_id=i, phase=phase)
        path = save_trial(result, output_dir)
        results.append(result)

        if result.turns:
            print(f"  Turns captured: {len(result.turns)}")
            print(f"  Overall hit rate: {result.overall_cache_hit_rate:.1f}%")
            print(f"  Total input: {result.total_input:,} tokens")
            for t in result.turns:
                print(
                    f"    Turn {t.turn}: hit={t.cache_hit_rate:.1f}% "
                    f"read={t.cache_read_input_tokens:,} "
                    f"create={t.cache_creation_input_tokens:,} "
                    f"input={t.input_tokens:,}"
                )
        else:
            print(f"  WARNING: No turns captured. Error: {result.error}")

        if i < trials:
            print("  Waiting 5s before next trial...")
            time.sleep(5)

    stats = Statistics.compute_summary(results)
    save_summary(stats, output_dir)
    print_summary_table(stats)
    return results, stats


def compare_phases():
    baseline_results = load_trials(BASELINE_DIR)
    optimized_results = load_trials(OPTIMIZED_DIR)

    if not baseline_results:
        print("ERROR: No baseline results found. Run --phase baseline first.")
        return
    if not optimized_results:
        print("ERROR: No optimized results found. Run --phase optimized first.")
        return

    baseline_stats = Statistics.compute_summary(baseline_results)
    optimized_stats = Statistics.compute_summary(optimized_results)

    print_summary_table(baseline_stats)
    print_summary_table(optimized_stats)

    comparison = Statistics.paired_comparison(baseline_results, optimized_results)
    print(Statistics.format_comparison_table(comparison))

    comp_path = RESULTS_DIR / "comparison.json"
    with open(comp_path, "w") as f:
        json.dump(comparison, f, indent=2)
    print(f"  Comparison saved: {comp_path}")

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    print("\nGenerating figures...")
    Visualization.plot_cache_hit_rate_comparison(
        baseline_stats,
        optimized_stats,
        FIGURES_DIR / "cache_hit_rate_comparison.png",
    )
    Visualization.plot_token_waste_per_turn(
        baseline_results,
        optimized_results,
        FIGURES_DIR / "token_waste_per_turn.png",
    )
    Visualization.plot_cumulative_savings(
        baseline_results,
        optimized_results,
        FIGURES_DIR / "cumulative_savings.png",
    )
    Visualization.plot_statistical_significance(
        baseline_results,
        optimized_results,
        comparison,
        FIGURES_DIR / "statistical_significance.png",
    )
    Visualization.plot_cache_heatmap(
        baseline_results,
        optimized_results,
        FIGURES_DIR / "cache_heatmap.png",
    )
    print(f"\nAll figures saved to {FIGURES_DIR}/")


def _resolve_cli(cli_path: str) -> str:
    if cli_path.endswith(".js"):
        return f"node {cli_path}"
    return cli_path


def main():
    parser = argparse.ArgumentParser(
        description="Cache Evaluation Script for Claude Code CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--phase",
        choices=["baseline", "optimized", "compare", "all"],
        required=True,
        help="Which phase to run",
    )
    parser.add_argument(
        "--trials", type=int, default=10, help="Number of trials (default: 10)"
    )
    parser.add_argument(
        "--cli",
        default="/tmp/claude-test/baseline/cli.js",
        help="Path to baseline CLI (default: /tmp/claude-test/baseline/cli.js)",
    )
    parser.add_argument(
        "--cli-optimized",
        default="/tmp/claude-test/optimized/cli.js",
        help="Path to optimized CLI (default: /tmp/claude-test/optimized/cli.js)",
    )
    parser.add_argument(
        "--model", default="claude-sonnet-4-20250514", help="Model to use"
    )
    parser.add_argument(
        "--working-dir", default=None, help="Working directory for CLI sessions"
    )

    args = parser.parse_args()

    cli_baseline = _resolve_cli(args.cli)
    cli_optimized = _resolve_cli(args.cli_optimized)

    print("Cache Evaluation for Claude Code CLI")
    print(f"  Phase:    {args.phase}")
    print(f"  Trials:   {args.trials}")
    print(f"  CLI baseline:  {cli_baseline}")
    print(f"  CLI optimized: {cli_optimized}")
    print(f"  Model:    {args.model}")
    print(f"  WorkDir:  {args.working_dir or os.getcwd()}")
    print()

    if args.phase == "baseline":
        run_phase(
            "baseline",
            args.trials,
            cli_baseline,
            args.model,
            args.working_dir,
        )
    elif args.phase == "optimized":
        run_phase(
            "optimized",
            args.trials,
            cli_optimized,
            args.model,
            args.working_dir,
        )
    elif args.phase == "compare":
        compare_phases()
    elif args.phase == "all":
        print(">>> Phase 1/3: BASELINE")
        run_phase(
            "baseline",
            args.trials,
            cli_baseline,
            args.model,
            args.working_dir,
        )
        print("\n>>> Phase 2/3: OPTIMIZED")
        run_phase(
            "optimized",
            args.trials,
            cli_optimized,
            args.model,
            args.working_dir,
        )
        print("\n>>> Phase 3/3: COMPARISON")
        compare_phases()


if __name__ == "__main__":
    main()
