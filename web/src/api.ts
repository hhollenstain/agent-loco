export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  const text = await response.text();
  let payload: { error?: string } = {};
  if (text) {
    try {
      payload = JSON.parse(text) as { error?: string };
    } catch {
      payload = {};
    }
  }
  if (!response.ok) {
    throw new ApiError(payload.error || "request failed", response.status);
  }
  return payload as T;
}
