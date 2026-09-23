import * as vscode from 'vscode';
import { EventEmitter } from 'vscode';

interface ServerEvents {
  status_changed: { status: string; session_id?: string };
  progress: { percentage: number; message: string };
  log: { level: string; message: string };
  session_end: { session_id: string; result: string };
  task_queued: { task_id: string };
}

interface ClientEvents {
  init_project: { path: string };
  queue_task: { goal: string; workspace: string };
  get_workspaces: Record<string, never>;
  get_sessions: Record<string, never>;
  get_models: Record<string, never>;
}

export class LocoServer {
  private ws: WebSocket | null = null;
  private readonly url: string;
  private readonly emitter: EventEmitter<ServerEvents[keyof ServerEvents]> = new EventEmitter();
  public readonly onServerEvent = this.emitter.event;

  private reconnectDelay = 2000;
  private retryTimer: NodeJS.Timeout | null = null;

  constructor(url: string) {
    this.url = url;
    this.connect();
  }

  connect() {
    try {
      this.ws = new WebSocket(this.url);
      this.ws.binaryType = 'arraybuffer';
      
      this.ws.onopen = () => {
        vscode.window.showInformationMessage('Agent Loco: Connected to server');
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event && data.event in this.emitter) {
            this.emitter.fire(data.event);
          }
        } catch (err) {
          console.error('Failed to parse WebSocket message', err);
        }
      };

      this.ws.onclose = () => {
        vscode.window.showWarningMessage('Agent Loco: Disconnected from server');
        this.retryTimer = setTimeout(() => this.connect(), this.reconnectDelay);
      };

      this.ws.onerror = (err) => {
        console.error('WebSocket error', err);
      };
    } catch (err) {
      console.error('Failed to connect to WebSocket', err);
      this.scheduleReconnect();
    }
  }

  disconnect() {
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    this.ws?.close();
    this.ws = null;
    this.emitter.fire({ status_changed: { status: 'disconnected' } });
  }

  scheduleReconnect() {
    this.retryTimer = setTimeout(() => this.connect(), this.reconnectDelay);
  }

  on<K extends keyof ServerEvents>(event: K, handler: (data: ServerEvents[K]) => void): vscode.Disposable {
    return this.emitter.event((data: ServerEvents[K]) => {
      handler(data);
    });
  }

  emit<M extends keyof ClientEvents>(event: M, data: ClientEvents[M]) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      vscode.window.showErrorMessage('Agent Loco: Server not connected');
      return;
    }
    this.ws.send(JSON.stringify({ event, data }));
  }

  async getWorkspaces(): Promise<string[]> {
    return new Promise((resolve, reject) => {
      if (!this.ws) return reject(new Error('Not connected'));
      this.emit('get_workspaces', {});
      // Placeholder for actual implementation
      resolve([]);
    });
  }

  async getModels(): Promise<string[]> {
    return new Promise((resolve, reject) => {
      this.emit('get_models', {});
      resolve([]);
    });
  }
}

export class Workspace {
  private current: string;
  private readonly storage: vscode.WorkspaceConfiguration;

  constructor(storage: vscode.WorkspaceStorage) {
    this.storage = storage;
    this.current = storage.get<string>('currentWorkspace', '') || '';
  }

  get path(): string {
    return this.current;
  }

  set path(p: string) {
    this.storage.update('currentWorkspace', p);
    vscode.window.showInformationMessage(`Agent Loco: Workspace set to ${p}`);
  }

  async pick(): Promise<string | undefined> {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
      vscode.window.showWarningMessage('No workspace folders available');
      return undefined;
    }
    const pick = await vscode.window.showQuickPick(
      folders.map(f => ({ label: f.name, description: f.uri.fsPath })),
      { placeHolder: 'Select a workspace folder' }
    );
    if (pick) {
      this.path = pick.description || '';
      return this.path;
    }
    return undefined;
  }
}
