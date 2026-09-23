# VSCode/Cursor Extension for agent-loco

This extension integrates with the agent-loco coding agent, surfacing the same capabilities as the CLI and web UI directly in the IDE.

## Architecture

```text
agent-loco CLI ──> Local HTTP server ──> Extension RPC bridge ──> IDE commands
      │                         │                           │
      │              .loco/config.yaml                    user tasks
      │                         │                           │
      └────> models via LOCO_MODEL_BASE_URL ─────────────────┘
```

The extension communicates with a running `loco` process via WebSocket on the web UI API. No direct filesystem access is needed; the extension calls `/api/...` endpoints that route through the existing `loco` CLI.

## File structure

```text
agent-loco-extension/
├── package.json           # Extension manifest
├── tsconfig.json          # TypeScript configuration
├── .eslintrc.json         # ESLint rules
├── src/
│   ├── extension.ts       # Entry point (activate/deactivate)
│   ├── rpc.ts             # WebSocket communication layer
│   └── commands.ts        # Command definitions
└── out/                   # Compiled JavaScript (generated)
```

## Commands

The extension exposes four commands in VSCode/Cursor:

- `Agent Loco: Choose Workspace` — Select a workspace folder for agent-loco operations
- `Agent Loco: Initialize Project` — Initialize agent-loco in the selected workspace
- `Agent Loco: Queue Task` — Submit a task goal for the agent to execute
- `Agent Loco: List Models` — View available LLM models from the configured server

Each command is implemented in `src/commands.ts` and registered via VSCode's command API. The `activate` function in `src/extension.ts` initializes the WebSocket connection and wires up all extensions.

## WebSocket Events

The extension listens for these server events:

- `status_changed` — Status updates with optional session_id
- `progress` — Progress updates with percentage and message
- `log` — Log messages at various levels
- `session_end` — Completion signals with result
- `task_queued` — Confirmation that a task was queued

Server events are defined in `src/rpc.ts` with corresponding client-to-server commands for initialization and task submission.

## Configuration

Users configure the WebSocket URL in `settings.json`:

```json
{
  "agent-loco.serverUrl": "ws://127.0.0.1:8080/ws"
}
```

This URL should point to a running `loco ui` process.

## Required Server Endpoints

The extension expects these `/api/` routes to be available:

- `GET /api/health` — Verify server is alive
- `GET /api/workspaces` — List known workspaces
- `POST /api/init` — Initialize a project
- `POST /api/queue` — Queue a task for execution
- `GET /api/sessions` — List sessions
- `GET /api/models` — List available models
- `WS` — WebSocket channel for real-time updates

The web UI already provides these; the extension simply invokes them via WebSocket.

## Cursor-specific Notes

Cursor uses the same VSCode extension API, so this extension works in Cursor. One caveat: Cursor may run the extension in a different workspace root for sandboxed projects. Ensure the extension's `--workspace` argument points to the project where the agent should operate, not necessarily the current opened folder.

## Build and Install

1. Install dependencies:  
   `cd agent-loco-extension && npm install`

2. Compile TypeScript:  
   `npm run compile`

3. Open in VSCode/Cursor and run "Run Extension" from the Debug panel.

4. For production use, install the `.vsix` after building:  
   `npm run vsce:package` then install in VSCode.

## Development

- Watch mode: `npm run watch`
- Linting: `npm run lint`
- Compile: `npm run compile`

## Required server state

The extension expects a running `loco ui` process. Start it with:

```bash
uv run loco ui --workspace /path/to/your/project --base-url http://127.0.0.1:11434/v1
```

The extension will then connect to `ws://127.0.0.1:8080/ws` (default port for `loco ui`).

## Supported API endpoints

The following `/api/` routes are required for the extension to function:

- `GET /api/health` — returns `{"status":"ok"}` to verify the server is alive
- `GET /api/workspaces` — list known workspaces from `.loco/workspaces.json`
- `POST /api/init` — call `loco init <path>` and wait for completion
- `POST /api/queue` — `loco run --workspace <path> --goal <goal>` in the background
- `GET /api/sessions` — list active/past sessions with status
- `GET /api/models` — list available models from the model server
- `WS` — WebSocket channel for streaming progress updates

The web UI already provides these; the extension simply invokes them.

## Cursor-specific notes

Cursor uses the same VSCode extension API, so the above extension will work. One caveat: Cursor may run the extension in a different workspace root for sandboxed projects. Ensure the extension's `--workspace` argument points to the project where the agent should operate, not necessarily the current opened folder.

## Publishing

To publish the extension:

1. Install the VSCode CLI: `npm install -g @vscode/vsce`
2. Sign in with your account: `vscode login`
3. Build: `npm run compile`
4. Publish: `vscode publish`

Do not ship secrets (extension configuration should be user-set in `settings.json`).
