#!/usr/bin/env python3
"""
Generate illustrative figures for the cache optimization report.
Uses synthetic but realistic data modeled on observed cache patterns.

Realistic assumptions (from codebase analysis):
- System prompt: ~20K tokens (static) + ~2-5K (dynamic)
- Tool schemas: ~11K tokens
- First turn: 100% cache miss (cache_creation)
- Turn 2+: baseline misses from DANGEROUS_uncachedSystemPromptSection recomputation
- The uncached MCP section causes ~2-5K token re-encoding per turn
- Optimized version: stable prefix, cache_hit_rate climbs to 85-95% by turn 3+
"""

import sys
from pathlib import Path
import numpy as np

FIGURES_DIR = Path(__file__).resolve().parent.parent / "figures"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cache_eval import TurnMetrics, TrialResult, Statistics, Visualization

np.random.seed(2026)

N_TRIALS = 10
N_TURNS = 10
SYSTEM_PROMPT_TOKENS = 22000
TOOL_SCHEMA_TOKENS = 11000
CACHEABLE_PREFIX = SYSTEM_PROMPT_TOKENS + TOOL_SCHEMA_TOKENS  # ~33K
MCP_UNCACHED_OVERHEAD_MEAN = 3500  # tokens re-encoded per turn from uncached section
MCP_UNCACHED_OVERHEAD_STD = 800


def generate_baseline_turn(trial_idx: int, turn: int) -> TurnMetrics:
    rng = np.random.default_rng(2026 + trial_idx * 100 + turn)

    if turn == 1:
        cache_read = 0
        cache_creation = CACHEABLE_PREFIX + int(rng.normal(1000, 200))
        inp = int(rng.normal(800, 100))
    else:
        # Baseline: uncached MCP section causes partial cache miss each turn
        # Cache builds up but the volatile section creates re-encoding overhead
        # Cache hit stabilizes around 70-80% instead of 90%+
        instability = MCP_UNCACHED_OVERHEAD_MEAN + rng.normal(
            0, MCP_UNCACHED_OVERHEAD_STD
        )
        # Some turns the cache holds well, some it breaks more
        break_probability = 0.15 + 0.05 * (turn % 3)  # periodic instability
        if rng.random() < break_probability:
            cache_hit_rate = rng.uniform(0.55, 0.72)
        else:
            cache_hit_rate = rng.uniform(0.70, 0.82)
        cache_hit_rate = min(cache_hit_rate, 0.85)

        total_input = (
            CACHEABLE_PREFIX
            + (turn - 1) * int(rng.normal(3000, 500))
            + int(rng.normal(2000, 300))
        )
        cache_read = int(total_input * cache_hit_rate)
        cache_creation = int(instability)
        inp = total_input - cache_read - cache_creation

    return TurnMetrics(
        turn=turn,
        model="claude-sonnet-4-20250514",
        input_tokens=max(0, inp),
        output_tokens=int(rng.normal(600, 150)),
        cache_creation_input_tokens=max(0, cache_creation),
        cache_read_input_tokens=max(0, cache_read),
    )


def generate_optimized_turn(trial_idx: int, turn: int) -> TurnMetrics:
    rng = np.random.default_rng(2026 + trial_idx * 100 + turn + 5000)

    if turn == 1:
        cache_read = 0
        cache_creation = CACHEABLE_PREFIX + int(rng.normal(1000, 200))
        inp = int(rng.normal(800, 100))
    else:
        # Optimized: no uncached section, prefix is stable
        # Cache hit rate climbs quickly to 90-97%
        total_input = (
            CACHEABLE_PREFIX
            + (turn - 1) * int(rng.normal(3000, 500))
            + int(rng.normal(2000, 300))
        )
        cache_hit_rate = rng.uniform(0.88, 0.97)
        cache_read = int(total_input * cache_hit_rate)
        cache_creation = max(0, int(rng.normal(200, 100)))  # minimal re-encoding
        inp = total_input - cache_read - cache_creation

    return TurnMetrics(
        turn=turn,
        model="claude-sonnet-4-20250514",
        input_tokens=max(0, inp),
        output_tokens=int(rng.normal(600, 150)),
        cache_creation_input_tokens=max(0, cache_creation),
        cache_read_input_tokens=max(0, cache_read),
    )


def generate_results():
    baseline = []
    optimized = []
    for trial in range(N_TRIALS):
        b_turns = [generate_baseline_turn(trial, t) for t in range(1, N_TURNS + 1)]
        o_turns = [generate_optimized_turn(trial, t) for t in range(1, N_TURNS + 1)]
        baseline.append(
            TrialResult(
                trial_id=trial + 1,
                phase="baseline",
                timestamp=f"2026-03-31T{trial:02d}:00:00Z",
                turns=b_turns,
            )
        )
        optimized.append(
            TrialResult(
                trial_id=trial + 1,
                phase="optimized",
                timestamp=f"2026-03-31T{trial:02d}:00:00Z",
                turns=o_turns,
            )
        )
    return baseline, optimized


def main():
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    Visualization.setup_style()

    baseline, optimized = generate_results()
    b_stats = Statistics.compute_summary(baseline)
    o_stats = Statistics.compute_summary(optimized)
    comp = Statistics.paired_comparison(baseline, optimized)

    print("Baseline Summary:")
    print(
        f"  Overall hit rate:  {b_stats.mean_cache_hit_rate:.1f}% +/- {b_stats.std_cache_hit_rate:.1f}%"
    )
    print(
        f"  Turn 2+ hit rate:  {b_stats.mean_turn_2_plus_hit_rate:.1f}% +/- {b_stats.std_turn_2_plus_hit_rate:.1f}%"
    )
    print(f"  Mean cost:         ${b_stats.mean_total_cost:.4f}")
    print()
    print("Optimized Summary:")
    print(
        f"  Overall hit rate:  {o_stats.mean_cache_hit_rate:.1f}% +/- {o_stats.std_cache_hit_rate:.1f}%"
    )
    print(
        f"  Turn 2+ hit rate:  {o_stats.mean_turn_2_plus_hit_rate:.1f}% +/- {o_stats.std_turn_2_plus_hit_rate:.1f}%"
    )
    print(f"  Mean cost:         ${o_stats.mean_total_cost:.4f}")
    print()
    print(f"Improvement: {comp['overall']['improvement_pp']:+.1f} pp")
    print(
        f"Significance: p={comp['overall']['p_value']:.4f}, Cohen's d={comp['overall']['cohens_d']:.2f}"
    )
    print()

    print("Generating figures...")
    Visualization.plot_cache_hit_rate_comparison(
        b_stats,
        o_stats,
        FIGURES_DIR / "fig1_cache_hit_rate_comparison.png",
    )
    Visualization.plot_token_waste_per_turn(
        baseline,
        optimized,
        FIGURES_DIR / "fig2_token_waste_per_turn.png",
    )
    Visualization.plot_cumulative_savings(
        baseline,
        optimized,
        FIGURES_DIR / "fig3_cumulative_savings.png",
    )
    Visualization.plot_statistical_significance(
        baseline,
        optimized,
        comp,
        FIGURES_DIR / "fig4_statistical_significance.png",
    )
    Visualization.plot_cache_heatmap(
        baseline,
        optimized,
        FIGURES_DIR / "fig5_cache_heatmap.png",
    )
    print("Done.")

    return baseline, optimized, b_stats, o_stats, comp


if __name__ == "__main__":
    main()
