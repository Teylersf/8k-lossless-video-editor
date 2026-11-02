export const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(
  /\/$/,
  "",
);
export const assetUrl = (path?: string | null) =>
  path ? `${API_BASE}${path.startsWith("/") ? path : `/${path}`}` : "";
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const result = await fetch(`${API_BASE}${path}`, options);
  if (!result.ok) {
    let message = `${result.status} ${result.statusText}`;
    try {
      const json = await result.json();
      message =
        typeof json.detail === "string"
          ? json.detail
          : JSON.stringify(json.detail || json.error || json);
    } catch {
      /* HTTP status remains useful. */
    }
    throw new Error(message);
  }
  return result.json() as Promise<T>;
}
export const post = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  api<T>(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Editor-Client": "8k-lossless-editor",
    },
    body: JSON.stringify(body),
    signal,
  });
export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "The operation could not finish.";
export const isTerminal = (status: string) =>
  ["completed", "failed", "cancelled"].includes(status);
export function downloadJson(value: unknown, name: string) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export const bytesLabel = (bytes: number) =>
  bytes >= 1024 ** 3
    ? `${(bytes / 1024 ** 3).toFixed(2)} GB`
    : bytes >= 1024 ** 2
      ? `${(bytes / 1024 ** 2).toFixed(1)} MB`
      : `${Math.round(bytes / 1024)} KB`;
