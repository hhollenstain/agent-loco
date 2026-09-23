import * as vscode from 'vscode';
import { Workspace, LocoServer } from './rpc';
import { defineCommands } from './commands';

let serverInstance: LocoServer | null = null;

export function activate(context: vscode.ExtensionContext) {
  const config = vscode.workspace.getConfiguration('agent-loco');
  const serverUrl = config.get<string>('serverUrl', 'ws://127.0.0.1:8080/ws');
  
  serverInstance = new LocoServer(serverUrl);
  const workspace = new Workspace(context.workspaceState);

  const commands = defineCommands(context, { server: serverInstance, workspace });
  commands.register();

  context.subscriptions.push(serverInstance);
}

export function deactivate() {
  serverInstance?.disconnect();
}
