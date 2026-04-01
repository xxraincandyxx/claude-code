# Claude Code

A CLI tool for AI-assisted coding, built by Anthropic.

## Overview

Claude Code is an interactive command-line tool that integrates Claude AI capabilities into your terminal workflow. It helps developers write, review, and refactor code with AI assistance while maintaining security and user control.

## Features

- **AI-Powered Code Assistance**: Get help with writing, understanding, and debugging code
- **Multi-Tool Execution**: Execute bash commands, read/write files, search code, and more
- **Sandbox Security**: Optional bubblewrap-based sandboxing for command isolation
- **Permission System**: Fine-grained control over file system and network access
- **MCP Integration**: Connect to Model Context Protocol servers for extended capabilities
- **Session Management**: Persistent conversations with context retention
- **Plugin Support**: Extend functionality through custom plugins

## Project Structure

```
claude/
├── assistant/          # Session history and memory management
├── bootstrap/          # Application initialization
├── bridge/             # IPC and session bridging
├── buddy/              # Notification system
├── cli/                # CLI argument handlers
├── commands/           # Slash commands implementation (100+ commands)
├── components/         # React UI components
├── constants/          # Application constants and prompts
├── context/            # Context management
├── coordinator/        # Task coordination
├── entrypoints/        # Application entry points
├── experiments/        # Cache optimization evaluation (A/B testing)
├── hooks/              # React hooks
├── ink/                # Terminal rendering library
├── keybindings/        # Keyboard shortcuts
├── memdir/             # Memory directory management
├── migrations/         # Database migrations
├── native-ts/          # Native TypeScript utilities
├── plugins/            # Plugin system
├── schemas/            # JSON schemas
├── screens/            # Application screens
├── scripts/            # Build and utility scripts
├── services/           # Core business logic services
├── skills/             # Agent skills
├── state/              # Application state management
├── tasks/              # Task execution system
├── tools/              # Tool implementations
├── types/              # TypeScript type definitions
├── upstreamproxy/      # Proxy infrastructure
├── utils/              # Utility functions
└── voice/              # Voice mode support
```

## Running the CLI

### Installed Binary

The official CLI installs to `~/.local/share/claude/versions/<version>` as a compiled Mach-O binary (macOS) or equivalent native binary on other platforms.

### NPM Package (Patchable)

For development and experimentation, the npm package provides a pre-bundled `cli.js` (12MB, minified) that runs via Node.js:

```bash
# Download and extract
npm pack @anthropic-ai/claude-code@2.1.63
tar xzf anthropic-ai-claude-code-2.1.63.tgz

# Run directly
node package/cli.js --version
```

### Building from Source

**Not possible with this snapshot.** The source code is incomplete — missing `package.json`, `tsconfig.json`, `bunfig.toml`, and private Anthropic dependencies (`@ant/*` packages). The binary is compiled with Bun's native compiler, which requires the full internal build pipeline.

## Experiments

The `experiments/` directory contains an A/B evaluation framework for measuring prompt cache optimization:

```bash
cd experiments

# Setup Python environment
uv sync

# Run baseline trials (10 trials × 10 turns)
uv run python cache_eval.py --phase baseline --trials 10 \
  --cli /tmp/claude-test/baseline/cli.js \
  --working-dir /path/to/project

# Run optimized trials
uv run python cache_eval.py --phase optimized --trials 10 \
  --cli-optimized /tmp/claude-test/optimized/cli.js \
  --working-dir /path/to/project

# Compare results and generate figures
uv run python cache_eval.py --phase compare
```

### Results (N=10, 200 API calls)

| Metric | Baseline | Optimized | Delta |
|--------|----------|-----------|-------|
| Cache hit rate | 88.6% ± 5.8% | 91.5% ± 1.5% | +2.9 pp |
| Cost/session | $0.153 | $0.131 | -14.3% |
| Variance (σ) | 5.8% | 1.5% | -74% |

See [experiments/report.md](experiments/report.md) for the full analysis.

## Core Architecture

### Tools System

Claude Code uses a tool-based architecture where AI can request execution of various operations:

- **BashTool**: Execute shell commands with security validation
- **FileReadTool**: Read files with permission checking
- **FileEditTool**: Edit files with safety checks
- **WebFetchTool**: Fetch URLs with SSRF protection
- **MCPTool**: Execute MCP server tools

### Security Model

1. **Sandboxing**: Optional bubblewrap sandbox for syscall filtering
2. **Permissions**: User-configurable allow/deny rules for file and network access
3. **Path Validation**: Protection against path traversal, UNC paths, and shell injection
4. **Credential Handling**: Secure storage via OS keychain
5. **SSRF Protection**: Blocks access to private IP ranges

### Permission Rules

Configure in `~/.claude/settings.json`:

```json
{
  "permissions": {
    "allow": ["Bash(git:*), Read(/project/src/**)"],
    "deny": ["Bash(sudo:*), Edit(/etc/**)"]
  }
}
```

## Configuration

### Settings Location

- macOS: `~/.claude/`
- Linux: `~/.config/claude/`
- Windows: `%APPDATA%/Claude/`

### Key Files

- `settings.json` - Main configuration
- `settings.local.json` - Local overrides
- `permissions.json` - Permission rules
- `commands/` - Custom slash commands
- `agents/` - Custom agents
- `skills/` - Custom skills

## Security Considerations

### Credential Storage

Credentials are stored in:
- macOS: Keychain
- Linux: libsecret
- Windows: Credential Manager

### Environment Variable Scrubbing

In GitHub Actions environments, sensitive environment variables are stripped from subprocesses to prevent prompt injection attacks.

### Shell Command Validation

The bash security system validates:
- Command substitution (`$()`, backticks)
- Process substitution (`<()`, `>()`)
- Shell expansion (`$VAR`, `${VAR}`)
- Dangerous patterns (sudo, git hooks, etc.)

## License

Proprietary - Anthropic

## Support

- Documentation: [code.claude.com](https://code.claude.com/docs/en/overview)
- Issues: [GitHub Issues](https://github.com/anthropics/claude-code/issues)
