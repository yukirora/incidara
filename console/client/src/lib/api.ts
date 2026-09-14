export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(
  method: string,
  path: string,
  body?: unknown
): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "include",
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    let message = res.statusText;
    try {
      const data = (await res.json()) as { error?: string; message?: string };
      message = data.error ?? data.message ?? message;
    } catch {
      // ignore JSON parse failures
    }
    throw new ApiError(res.status, message);
  }

  // 204 No Content
  if (res.status === 204) {
    return undefined as T;
  }

  return res.json() as Promise<T>;
}
