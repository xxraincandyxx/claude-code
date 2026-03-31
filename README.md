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
├── commands/            # Slash commands implementation (100+ commands)
├── components/         # React UI components
├── constants/          # Application constants and prompts
├── context/            # Context management
├── coordinator/        # Task coordination
├── entrypoints/        # Application entry points
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

### Sandbox Configuration

```json
{
  "sandbox": {
    "enabled": true,
    "allowUnsandboxedCommands": false,
    "filesystem": {
      "allowWrite": ["/tmp/claude"],
      "denyWrite": ["/etc", "/var"]
    },
    "network": {
      "allowedDomains": ["api.github.com"]
    }
  }
}
```

## Development

### Prerequisites

- Node.js 18+ (for build tools)
- bubblewrap (bwrap) for sandboxing on Linux
- macOS or Linux (WSL2 supported)

### Building

```bash
# Install dependencies
npm install

# Build the project
npm run build

# Run in development mode
npm run dev
```

### Testing

```bash
# Run unit tests
npm test

# Run integration tests
npm run test:integration

# Run with coverage
npm run test:coverage
```

## Commands

### Slash Commands

- `/ask` - Ask a question
- `/bug` - Report a bug
- `/clear` - Clear conversation
- `/commit` - Create a commit
- `/diff` - Show changes
- `/help` - Show help
- `/init` - Initialize project
- `/login` - Login to Claude
- `/logout` - Logout
- `/model` - Select AI model
- `/plan` - Show task plan
- `/review` - Review code
- `/search` - Search code
- `/shell` - Run shell command
- And 100+ more...

### Keyboard Shortcuts

- `Ctrl+C` - Cancel current operation
- `Ctrl+D` - Exit REPL
- `Ctrl+L` - Clear screen
- `Tab` - Autocomplete
- `Up/Down` - History navigation

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

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit a pull request

## License

Proprietary - Anthropic

## Support

- Documentation: [docs.anthropic.com](https://docs.anthropic.com)
- Issues: GitHub Issues
- Email: support@anthropic.com
