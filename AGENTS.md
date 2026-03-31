# AGENTS.md - Claude Code Repository Guidelines

## Project Overview

Claude Code is a Bun-based TypeScript CLI tool for AI-assisted coding. Uses React/Ink for terminal UI, Commander for CLI parsing, and a custom tool-based architecture for AI operations.

## Build/Lint/Test Commands

```bash
npm install        # Install dependencies
npm run build      # Build the project
npm run dev        # Development mode
npm test           # Run unit tests
npm run test:integration  # Integration tests
npm run test:coverage     # Tests with coverage
```

**Note:** No test files (`*.test.*`, `*.spec.*`) or test configs found in this snapshot. Build config files (`package.json`, `tsconfig.json`, `bunfig.toml`) are external to this code snapshot. All imports use `.js` extensions (Bun/ESM convention).

## Code Style

### Imports

- **Order:** External packages → `src/` absolute imports → relative imports (sorted by depth)
- **Extensions:** Always include `.js` extension, even for `.ts`/`.tsx` sources
- **Type-only:** Use `import type { ... }` for type-only imports
- **React:** `import * as React from 'react'`
- **Lodash:** Individual function imports from `lodash-es`
- **Lazy imports:** Use dynamic `import()` for feature-flagged or conditional modules

```ts
import chalk from 'chalk';
import type { ToolDef } from '../types/tools.js';
import { getOauthConfig } from '../constants/oauth.js';
```

### TypeScript

- **Strict mode** enabled throughout
- **Prefer `type` over `interface`** for object shapes, unions, intersections
- **Branded types** for domain safety: `type SessionId = string & { readonly __brand: 'SessionId' }`
- **Discriminated unions** for result types: `{ result: true } | { result: false; message: string }`
- **`satisfies` operator** for type-safe object literals
- **`readonly`** on immutable properties
- **Generics** with defaults: `Tool<Input = AnyObject, Output = unknown>`

### Naming Conventions

| Kind | Convention | Example |
|------|------------|---------|
| Functions/variables | camelCase | `getLogDisplayTitle`, `errorLogSink` |
| Constants | UPPER_SNAKE_CASE | `GOODBYE_MESSAGES`, `PROGRESS_THRESHOLD_MS` |
| Classes/types | PascalCase | `ClaudeError`, `ToolDef`, `SessionId` |
| React components | PascalCase | `Spinner`, `Button`, `AppStateProvider` |
| React hooks | camelCase + `use` prefix | `useSettings`, `useAppState` |
| Files | camelCase for utils, PascalCase for components | `errors.ts`, `Spinner.tsx` |

### Exports

- **Named exports** are strongly preferred; `export default` is rare
- **Re-exports** used to break cycles and centralize types
- **Barrel exports** in state modules for convenience

### Async Patterns

- **async/await** is dominant for sequential operations
- **`Promise.all`** for parallel operations
- **`void` prefix** for fire-and-forget: `void initUser();`
- **Async generators** for streaming/progress: `async function* runShellCommand()`
- **`Promise.race`** for timeout/progress patterns

### Error Handling

- **Custom error hierarchy:** `ClaudeError` → `ShellError`, `ConfigParseError`, etc.
- **Type guards** for error classification: `isAbortError()`, `isENOENT()`, `isFsInaccessible()`
- **Error normalization:** `toError(e: unknown): Error`, `errorMessage(e: unknown): string`
- **Fail-open pattern** for non-critical services (log and continue)
- **Telemetry-safe errors** for analytics (no PII in messages)

```ts
catch (e) {
  if (isENOENT(e)) { return; }
  if (isAbortError(e)) { throw e; }
  logError(toError(e));
}
```

### Comments

- **JSDoc** for public/exported functions
- **Inline comments** explain non-obvious decisions, not obvious code
- **ESLint disables:** Use `// eslint-disable-next-line custom-rules/no-top-level-side-effects` for specific violations
- **Biome ignores:** `// biome-ignore lint/suspicious/noConsole::` for intentional console usage
- **Section markers:** `// @[MODEL LAUNCH]:` tags for searchable code sections

### React/Ink Components

- **Ink primitives:** `<Box>`, `<Text>` for terminal rendering
- **Props:** Define as `type Props = { ... }` (not interfaces)
- **Hooks:** Thin selectors over `useAppState`
- **State:** External store pattern with `useSyncExternalStore`
- **React Compiler:** Some files use `react/compiler-runtime` transforms

### Tool Definitions

Tools use the `buildTool()` factory pattern with `satisfies ToolDef`:

```ts
export const BashTool = buildTool({
  name: BASH_TOOL_NAME,
  inputSchema: ...,
  async call(input, context, canUseTool, parentMessage, onProgress) { ... },
  isConcurrencySafe(input) { ... },
  isReadOnly(input) { ... },
}) satisfies ToolDef<InputSchema, Out, BashProgress>;
```

### Linting Rules

Custom eslint rules enforced:
- `custom-rules/no-top-level-side-effects` — no side effects at module top
- `custom-rules/no-process-env-top-level` — no `process.env` at module top
- `custom-rules/no-process-exit` — controlled `process.exit()` usage
- `custom-rules/safe-env-boolean-check` — safe env var boolean checks
- `custom-rules/safe-process-env` — safe process.env access

### Feature Flags

Use `feature('FLAG_NAME')` from `bun:bundle` for build-time dead code elimination:

```ts
import { feature } from 'bun:bundle';
if (feature('DAEMON') && args[0] === 'daemon') { ... }
```

### Security

- **Path validation:** Protection against traversal, UNC paths, shell injection
- **SSRF protection:** Blocks access to private IP ranges
- **Credential handling:** OS keychain storage (Keychain/libsecret/Credential Manager)
- **Sandboxing:** Optional bubblewrap-based syscall filtering
- **Permission system:** Fine-grained allow/deny rules in `~/.claude/settings.json`
