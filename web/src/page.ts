export function workspacePath(): string {
  const input = document.getElementById("workspace");
  return input instanceof HTMLInputElement ? input.value : "";
}

export function notify(kind: string, message: string, title: string): void {
  const page = window as Window & {
    notify?: (kind: string, message: string, options?: { title?: string }) => void;
  };
  page.notify?.(kind, message, { title });
}
