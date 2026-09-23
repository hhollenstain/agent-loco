import * as vscode from 'vscode';
import { Workspace, LocoServer } from './rpc';

export interface ExtensionDeps {
  server: LocoServer;
  workspace: Workspace;
}

export interface DefineCommandsResult {
  register(): void;
}

export function defineCommands(ctx: vscode.ExtensionContext, deps: ExtensionDeps): DefineCommandsResult {
  const { server, workspace } = deps;

  return {
    register() {
      const initSubscription = vscode.commands.registerCommand(
        'agent-loco.initProject',
        async () => {
          if (!workspace.path) {
            vscode.window.showWarningMessage('No workspace selected. Run "Agent Loco: Choose Workspace" first.');
            return;
          }
          vscode.window.showInformationMessage(`Initializing project at ${workspace.path}`);
          server.emit('init_project', { path: workspace.path });
        }
      );

      const queueSubscription = vscode.commands.registerCommand(
        'agent-loco.queueTask',
        async () => {
          if (!workspace.path) {
            vscode.window.showWarningMessage('No workspace selected. Run "Agent Loco: Choose Workspace" first.');
            return;
          }
          const task = await vscode.window.showInputBox({
            prompt: 'Enter task goal',
            ignoreFocusOut: true,
            placeHolder: 'e.g., "Refactor the auth module to use JWT tokens"'
          });
          if (task) {
            vscode.window.showInformationMessage(`Queuing task: ${task}`);
            server.emit('queue_task', { goal: task, workspace: workspace.path });
          }
        }
      );

      const chooseSubscription = vscode.commands.registerCommand(
        'agent-loco.chooseWorkspace',
        async () => {
          await workspace.pick();
        }
      );

      const listModelsSubscription = vscode.commands.registerCommand(
        'agent-loco.listModels',
        async () => {
          vscode.window.showInformationMessage('Fetching available models...');
          const models = await server.getModels();
          const quickPickItems = models.map(m => ({ label: m, description: 'Select model' }));
          const selected = await vscode.window.showQuickPick(quickPickItems, {
            placeHolder: 'Select a model to use'
          });
          if (selected) {
            vscode.window.showInformationMessage(`Selected model: ${selected.label}`);
          }
        }
      );

      ctx.subscriptions.push(initSubscription);
      ctx.subscriptions.push(queueSubscription);
      ctx.subscriptions.push(chooseSubscription);
      ctx.subscriptions.push(listModelsSubscription);
    }
  };
}
