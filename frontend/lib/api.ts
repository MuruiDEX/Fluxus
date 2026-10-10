/**
 * Backend API client (Stage 6 frontend slice).
 *
 * - Base URL from `NEXT_PUBLIC_API_URL` (default: local backend).
 * - Session cookie is HttpOnly: the browser sends it automatically, so we
 *   only set `credentials: "include"`. No session id, password, or CSRF
 *   secret ever touches localStorage — CSRF lives in memory only.
 * - Mutations carry the in-memory `X-CSRF-Token`; 401s notify the session
 *   store through `onUnauthorized` (wired by the auth store, no imports).
 */

export interface ApiErrorDetails {
  status: number;
  code: string;
  message: string;
}

export class ApiError extends Error {
  status: number;
  code: string;

  constructor({ status, code, message }: ApiErrorDetails) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

let csrfToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export function onUnauthorized(handler: (() => void) | null) {
  unauthorizedHandler = handler;
}

export function apiBaseUrl(): string {
  const raw = process.env.NEXT_PUBLIC_API_URL ?? "";
  return raw.replace(/\/$/, "") || "http://127.0.0.1:8000";
}

export interface ApiUser {
  id: string;
  email: string;
  displayName: string;
}

export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const method = init?.method ?? "GET";
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Client-Contract": "1",
  };
  if (UNSAFE.has(method) && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  let res: Response;
  try {
    res = await fetch(`${apiBaseUrl()}${path}`, {
      method,
      headers,
      credentials: "include",
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
  } catch (cause) {
    throw new ApiError({ status: 0, code: "network_unreachable", message: String(cause) });
  }
  if (res.status === 401) {
    setCsrfToken(null);
    unauthorizedHandler?.();
  }
  if (!res.ok) {
    let code = "http_error";
    let message = `Request failed with status ${res.status}.`;
    try {
      const data = await res.json();
      if (data?.error?.code) code = String(data.error.code);
      if (data?.error?.message) message = String(data.error.message);
    } catch {
      // Non-JSON error body: keep the generic message.
    }
    throw new ApiError({ status: res.status, code, message });
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function apiLogin(email: string, password: string) {
  const data = await api<{ user: ApiUser; csrf_token: string; businesses: string[] }>(
    "/api/v1/auth/login",
    { method: "POST", body: { email, password } },
  );
  setCsrfToken(data.csrf_token);
  return data;
}

export async function apiLogout() {
  try {
    await api("/api/v1/auth/logout", { method: "POST" });
  } finally {
    setCsrfToken(null);
  }
}

export async function apiMe() {
  return api<{ user: ApiUser; businesses: string[] }>("/api/v1/auth/me");
}
