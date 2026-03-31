# Cache Evaluation Experiment Plan

## Objective

Scientifically measure and compare prompt cache hit rates between:
- **Baseline**: The current Claude Code CLI (with all existing cache-breaking mechanisms)
- **Optimized**: A patched version with cache-breaking mechanisms removed/minimized

The goal is to quantify the token waste caused by `DANGEROUS_uncachedSystemPromptSection` (MCP instructions recompute-every-turn), the `BREAK_CACHE_COMMAND` injection path, and other volatile prompt sections, then demonstrate the savings from eliminating them.

---

## Architecture of Cache-Breaking Mechanisms (Current State)

Before the experiment, here is what we know from code analysis:

| Mechanism | File | Effect | Active By Default? |
|---|---|---|---|
| `BREAK_CACHE_COMMAND` feature flag + `systemPromptInjection` | `context.ts:22-146` | Injects `[CACHE_BREAKER: ...]` into system context, busting the prefix cache | No (ant-only, feature-flagged, always set to `null`) |
| `DANGEROUS_uncachedSystemPromptSection('mcp_instructions', ...)` | `prompts.ts:513-520` | MCP instructions section recomputes every turn; when content changes, it invalidates the cache for all subsequent blocks | **Yes** (only bypassed when `isMcpInstructionsDeltaEnabled()` returns true) |
| `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` | `prompts.ts:573` | Separates static (globally cacheable) from dynamic (per-session) content; post-boundary content gets `cacheScope: null` | Yes (first-party only, `shouldUseGlobalCacheScope()`) |
| `promptCacheBreakDetection` | `promptCacheBreakDetection.ts` | Monitors cache misses (5%+ and 2000+ token drops); passive detection, does NOT cause breaks | No (feature-flagged `PROMPT_CACHE_BREAK_DETECTION`) |
| Unicode sanitization | `sanitization.ts:25-65` | Strips zero-width chars; defensive, does not cause cache breaks | Yes (but neutral) |
| Session latches (1h TTL, AFK, fast-mode, cached-MC) | `bootstrap/state.ts:1692-1714` | Prevents mid-session cache key changes | Yes (stabilizes cache) |
| MCP instructions delta | `mcpInstructionsDelta.ts` | Announces MCP changes via persisted attachments instead of recomputing section | Conditional (`tengu_basalt_3kr` gate or ant) |
| Tool schema cache | `toolSchemaCache.ts` | Memoizes schemas per-session to avoid busting ~11K-token tool block cache | Yes (stabilizes cache) |

**Key finding**: The primary active cache-breaker in production is `DANGEROUS_uncachedSystemPromptSection('mcp_instructions')` when `isMcpInstructionsDeltaEnabled()` is false. Every turn recomputation can shift token boundaries and invalidate the server-side prefix cache.

---

## Phase 1: Baseline Evaluation Script

### 1.1 Script: `experiments/cache_eval.py`

A Python script that orchestrates CLI sessions and captures cache metrics.

**Data Collection Method**: Parse the JSONL log files that Claude Code writes to `~/.cache/claude-cli/`. Each API response contains:
- `cache_read_input_tokens` — tokens served from cache (good)
- `cache_creation_input_tokens` — tokens written to cache (one-time cost)
- `input_tokens` — tokens NOT served from cache (cache miss = bad)

**Derived Metrics**:
```
cache_hit_rate = cache_read_input_tokens / total_input_tokens
cache_miss_rate = input_tokens / total_input_tokens
total_input_tokens = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
effective_savings = cache_read_input_tokens * PRICE_PER_INPUT_TOKEN - cache_creation_input_tokens * PRICE_PER_CACHE_WRITE_TOKEN
```

### 1.2 Scientific Design

**Independent Variable**: CLI version (baseline vs. optimized)

**Controlled Variables**:
- Same model (claude-sonnet-4-20250514)
- Same conversation tasks (reproducible prompt sequence)
- Same working directory and git state
- Same environment variables (disable randomness sources)
- Same MCP server configuration (or none, for controlled tests)

**Dependent Variables**:
1. Cache hit rate per turn (primary metric)
2. Cache miss tokens per turn
3. Cumulative token waste over N-turn conversation
4. Cost impact ($ saved from cache hits)
5. Time-to-first-token (latency proxy for cache misses)

**Experimental Protocol**:

```
For each CLI version (baseline, optimized):
  Repeat N=10 trials:
    1. Start fresh CLI session
    2. Execute standardized prompt sequence (see below)
    3. Capture per-turn metrics from log files
    4. Calculate aggregate statistics

Compare distributions using:
  - Paired t-test (same prompt sequence, different CLI)
  - Effect size (Cohen's d)
  - 95% confidence intervals
```

**Standardized Prompt Sequence** (10 turns, designed to exercise cache):

| Turn | Prompt | Rationale |
|------|--------|-----------|
| 1 | "Read the file README.md" | First turn — all cache miss (baseline) |
| 2 | "Summarize what you read" | Should hit cache on system prompt |
| 3 | "List files in src/" | Exercise tool use, cache should hold |
| 4 | "What does utils/api.ts do?" | Another file read, cache pressure |
| 5 | "Explain the caching system" | Long response, cache should be stable |
| 6 | "Read context.ts" | More tool use |
| 7 | "How does sanitization work?" | Cache stability test |
| 8 | "Show me the system prompt sections" | Cache should still hold |
| 9 | "Edit context.ts to add a comment" | Mutation — cache may shift |
| 10 | "Summarize all changes made" | Final cache test |

### 1.3 Script Structure

```
experiments/
├── PLAN.md                    # This file
├── cache_eval.py              # Main evaluation script
├── prompts/                   # Standardized prompt sequences
│   └── standard_10turn.jsonl
├── results/                   # Output directory (gitignored)
│   ├── baseline/              # Baseline trial results
│   │   ├── trial_001.json
│   │   ├── ...
│   │   └── summary.json
│   └── optimized/             # Optimized trial results
│       ├── trial_001.json
│       ├── ...
│       └── summary.json
├── figures/                   # Generated charts
│   ├── cache_hit_rate_comparison.png
│   ├── token_waste_per_turn.png
│   ├── cumulative_savings.png
│   └── statistical_significance.png
└── report.md                  # Final optimization report
```

### 1.4 Implementation Plan for `cache_eval.py`

```python
# Core components:

1. Config class
   - model, num_trials, prompt_sequence_path, cli_binary_path
   - output_dir, log_dir (auto-discovered from ~/.cache/claude-cli/)

2. LogParser class
   - parse_jsonl_log(path) -> list[TurnMetrics]
   - Extract: turn_number, cache_read_tokens, cache_creation_tokens,
     input_tokens, output_tokens, latency_ms, timestamp

3. TurnMetrics dataclass
   - turn: int
   - cache_read_tokens: int
   - cache_creation_tokens: int
   - input_tokens: int
   - output_tokens: int
   - cache_hit_rate: float  # computed
   - total_input_tokens: int  # computed
   - latency_ms: float

4. TrialRunner class
   - run_trial(trial_id, cli_binary, prompt_sequence) -> list[TurnMetrics]
   - Spawns CLI subprocess, feeds prompts via stdin/pexpect
   - Waits for response completion between turns
   - Collects log files after session ends

5. Statistics class
   - compute_statistics(trial_results) -> SummaryStats
   - mean, median, std, ci_95 for each metric
   - paired_ttest(baseline, optimized) -> p_value, cohens_d
   - format_results_table()

6. Visualization class
   - plot_cache_hit_rate_comparison(baseline, optimized, output_path)
   - plot_token_waste_per_turn(baseline, optimized, output_path)
   - plot_cumulative_savings(baseline, optimized, output_path)
   - plot_statistical_significance(baseline, optimized, output_path)
   - Uses matplotlib with a clean, publication-ready style

7. Main orchestration
   - argparse: --phase {baseline,optimized,compare,all}
   - Phase 'baseline': run N trials with current CLI binary
   - Phase 'optimized': run N trials with patched CLI binary
   - Phase 'compare': load saved results, run stats, generate figures
   - Phase 'all': run everything sequentially
```

### 1.5 Figures to Generate

**Figure 1: Cache Hit Rate Per Turn (Line Chart)**
- X-axis: Turn number (1-10)
- Y-axis: Cache hit rate (0-100%)
- Two lines: baseline (red) vs optimized (green)
- Shaded regions: 95% CI across trials
- Title: "Prompt Cache Hit Rate by Conversation Turn"

**Figure 2: Token Waste Per Turn (Bar Chart)**
- Grouped bars per turn
- Y-axis: Miss tokens (cache_read that SHOULD have been hit)
- Error bars: standard deviation across trials
- Title: "Cache Miss Tokens per Turn"

**Figure 3: Cumulative Token Savings (Area Chart)**
- X-axis: Turn number
- Y-axis: Cumulative tokens saved by optimization
- Green filled area
- Annotation: total savings in $ and token count
- Title: "Cumulative Token Savings from Cache Optimization"

**Figure 4: Statistical Significance (Violin/Box Plot)**
- Side-by-side violin plots for overall cache hit rate
- Paired t-test p-value annotation
- Effect size (Cohen's d) annotation
- Title: "Cache Hit Rate Distribution: Baseline vs Optimized"

**Figure 5: Heatmap — Cache Hit Rate by (Turn × Trial)**
- Rows: Trial number (1-10)
- Columns: Turn number (1-10)
- Color: Cache hit rate (blue=low, red=high)
- Two heatmaps side by side: baseline vs optimized
- Title: "Cache Hit Rate Consistency Across Trials"

---

## Phase 2: Optimized CLI Version (Cache-Breaker Removal)

### 2.1 Patch Strategy

Create a git branch `experiment/remove-cache-breakers` with these changes:

**Patch 1: Convert MCP instructions from uncached to cached section**

File: `constants/prompts.ts:513-520`

```diff
- DANGEROUS_uncachedSystemPromptSection(
-   'mcp_instructions',
-   () =>
-     isMcpInstructionsDeltaEnabled()
-       ? null
-       : getMcpInstructionsSection(mcpClients),
-   'MCP servers connect/disconnect between turns',
- ),
+ systemPromptSection(
+   'mcp_instructions',
+   () =>
+     isMcpInstructionsDeltaEnabled()
+       ? null
+       : getMcpInstructionsSection(mcpClients),
+ ),
```

Effect: MCP instructions computed once per conversation instead of every turn. This is the biggest win — eliminates the only production-active `DANGEROUS_uncachedSystemPromptSection`.

**Patch 2: Remove BREAK_CACHE_COMMAND code path entirely**

File: `context.ts:22-34,131-147`

```diff
- // System prompt injection for cache breaking (ant-only, ephemeral debugging state)
- let systemPromptInjection: string | null = null
- 
- export function getSystemPromptInjection(): string | null {
-   return systemPromptInjection
- }
- 
- export function setSystemPromptInjection(value: string | null): void {
-   systemPromptInjection = value
-   // Clear context caches immediately when injection changes
-   getUserContext.cache.clear?.()
-   getSystemContext.cache.clear?.()
- }
```

And in `getSystemContext`:
```diff
- const injection = feature('BREAK_CACHE_COMMAND')
-   ? getSystemPromptInjection()
-   : null
  ...
- ...(feature('BREAK_CACHE_COMMAND') && injection
-   ? {
-       cacheBreaker: `[CACHE_BREAKER: ${injection}]`,
-     }
-   : {}),
```

Effect: Dead code removal. The feature flag is never enabled externally, and the injection is always `null`. Removing it eliminates any possibility of accidental activation and reduces bundle complexity.

**Patch 3: Ensure MCP instructions delta is always enabled**

File: `utils/mcpInstructionsDelta.ts:37-44`

```diff
 export function isMcpInstructionsDeltaEnabled(): boolean {
-  if (isEnvTruthy(process.env.CLAUDE_CODE_MCP_INSTR_DELTA)) return true
-  if (isEnvDefinedFalsy(process.env.CLAUDE_CODE_MCP_INSTR_DELTA)) return false
-  return (
-    process.env.USER_TYPE === 'ant' ||
-    getFeatureValue_CACHED_MAY_BE_STALE('tengu_basalt_3kr', false)
-  )
+  return true
 }
```

Effect: MCP instructions always announced via persisted delta attachments instead of volatile system prompt sections. This is the safest optimization — it doesn't change MCP instruction delivery, just the delivery mechanism.

### 2.2 Build & Verification

```bash
# Create branch
git checkout -b experiment/remove-cache-breakers

# Apply patches
# (apply the three diffs above)

# Build
npm run build

# Verify: run smoke test
echo "Read README.md" | node dist/cli.js --no-input

# Verify: check system prompt does NOT contain CACHE_BREAKER
# Verify: check logs for cache_read_input_tokens on turn 2+
```

### 2.3 Risk Assessment

| Patch | Risk | Mitigation |
|-------|------|------------|
| Patch 1 (MCP cached) | MCP server connecting mid-conversation won't appear in system prompt | MCP instructions delta (Patch 3) handles this via attachments |
| Patch 2 (remove BREAK_CACHE) | None — feature was never active externally | N/A |
| Patch 3 (always delta) | Delta attachment logic has edge cases (mid-session disconnect) | Existing tests cover this; delta path is already used by all `ant` users |

---

## Phase 3: Optimization Report

### 3.1 Report Structure (`experiments/report.md`)

```markdown
# Prompt Cache Optimization Report

## Executive Summary
- X% improvement in cache hit rate
- Y tokens saved per 10-turn conversation
- $Z cost reduction per conversation
- Statistical significance: p < 0.05

## 1. Background
### 1.1 What is Prompt Caching
[Explanation with diagram: server-side prefix caching]
### 1.2 Cache-Breaking Mechanisms
[Table from architecture analysis above]

## 2. Methodology
### 2.1 Experimental Design
[Controlled experiment description]
### 2.2 Metrics
[Definition of each metric]
### 2.3 Statistical Methods
[Paired t-test, Cohen's d, 95% CI]

## 3. Results
### 3.1 Cache Hit Rate
[Figure 1 inserted here]
[Table: per-turn cache hit rates]
### 3.2 Token Waste Analysis
[Figure 2 inserted here]
[Table: per-turn miss tokens]
### 3.3 Cumulative Savings
[Figure 3 inserted here]
[Cost calculation]
### 3.4 Statistical Significance
[Figure 4 inserted here]
[p-value, effect size]
### 3.5 Cross-Trial Consistency
[Figure 5 inserted here]

## 4. Changes Made
### 4.1 MCP Instructions: Uncached → Cached
[Before/after code diff, explanation]
### 4.2 BREAK_CACHE_COMMAND Removal
[Before/after, explanation]
### 4.3 MCP Delta Always-On
[Before/after, explanation]

## 5. Recommendations
- Merge Patch 3 (MCP delta always-on) — lowest risk, highest impact
- Merge Patch 2 (dead code removal) — zero risk
- Consider Patch 1 (MCP cached section) — requires MCP delta to be active

## 6. Appendix
- Raw data tables
- Full statistical output
- Reproduction instructions
```

### 3.2 Figure Generation Details

All figures use matplotlib with this style:

```python
import matplotlib.pyplot as plt
import matplotlib as mpl

# Publication-ready defaults
mpl.rcParams.update({
    'font.family': 'sans-serif',
    'font.size': 11,
    'axes.titlesize': 13,
    'axes.labelsize': 11,
    'figure.dpi': 150,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
})

COLORS = {
    'baseline': '#E74C3C',    # Red
    'optimized': '#27AE60',   # Green
    'ci_shade': 0.15,         # Alpha for confidence intervals
}
```

Each figure saved as both PNG (for report) and SVG (for web/docs).

---

## Execution Timeline

| Step | Action | Estimated Time |
|------|--------|---------------|
| 1 | Create `experiments/` directory structure | 5 min |
| 2 | Write `cache_eval.py` (log parser, trial runner, stats, viz) | 2-3 hours |
| 3 | Write `prompts/standard_10turn.jsonl` | 15 min |
| 4 | Run baseline trials (N=10) | ~30 min (depends on API latency) |
| 5 | Create `experiment/remove-cache-breakers` branch | 10 min |
| 6 | Apply 3 patches, build, smoke test | 20 min |
| 7 | Run optimized trials (N=10) | ~30 min |
| 8 | Run comparison analysis, generate figures | 10 min |
| 9 | Write final report with embedded figures | 1 hour |

**Total estimated effort: ~5 hours**

---

## Dependencies

- Python 3.10+
- matplotlib, numpy, scipy (`pip install matplotlib numpy scipy`)
- pexpect (`pip install pexpect`) for CLI subprocess management
- Access to Claude API (for running trial conversations)
- Built Claude Code CLI binary (baseline and patched versions)

---

## Reproducibility Checklist

- [ ] Prompt sequence committed to `experiments/prompts/`
- [ ] All environment variables documented (model, MCP config, etc.)
- [ ] Random seeds fixed where applicable
- [ ] Raw trial data saved to `experiments/results/`
- [ ] Figure generation is deterministic (same data → same figures)
- [ ] Statistical analysis code is parameter-free (reads from data)
- [ ] Report markdown references figures by relative path
