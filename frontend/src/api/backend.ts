import { getToken } from "@/pages/chat/chat-client"

const BASE = import.meta.env.VITE_BACKEND_API_URL ?? "/api"

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

export type PlatformAssistant = {
  id: string
  name: string
  description: string
  version: string
  baseAgent: "build" | "plan"
  systemPrompt: string
  skills: string[]
  tools: string[]
}

export type AssistantEnableResult = PlatformAssistant & {
  enabled: boolean
  changed: boolean
  mcpStatus: "connected"
  requiresNewSession: boolean
}

export async function apiFetch<T = unknown>(path: string, init?: RequestInit, timeoutMs = 15000): Promise<T> {
  const token = getToken()
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  let resp: Response
  try {
    resp = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: "include",
      signal: init?.signal ?? controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError("请求超时，请检查后端和数据库连接", 408)
    }
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText)
    throw new ApiError(text || `HTTP ${resp.status}`, resp.status)
  }
  return resp.json() as Promise<T>
}

async function apiDownload(path: string, fallbackName: string): Promise<void> {
  const token = getToken()
  const resp = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText)
    throw new ApiError(text || `HTTP ${resp.status}`, resp.status)
  }
  const disposition = resp.headers.get("Content-Disposition") ?? ""
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const quoted = disposition.match(/filename="([^"]+)"/i)?.[1]
  let filename = fallbackName
  try {
    if (encoded) filename = decodeURIComponent(encoded)
    else if (quoted) filename = quoted
  } catch {}
  const url = URL.createObjectURL(await resp.blob())
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const backend = {
  login: (email: string, password: string) =>
    apiFetch<{ token: string; user: { id: string; email: string } }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  register: (email: string, password: string) =>
    apiFetch<{ token: string; user: { id: string; email: string } }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => apiFetch<{ id: string; email: string }>("/auth/me", undefined, 8000),
  dispatch: () => apiFetch<{ sandboxId: string; proxyBase: string; workspace: string }>("/dispatch", { method: "POST" }),
  assistants: () => apiFetch<PlatformAssistant[]>("/assistants"),
  enableAssistant: (id: string) =>
    apiFetch<AssistantEnableResult>(`/assistants/${encodeURIComponent(id)}/install`, { method: "POST" }),
  workspace: () => apiFetch<{ id: string; sandboxId: string; directory: string }>("/workspace"),
  workspaceFiles: (path = "") =>
    apiFetch<Array<{ name: string; path: string; type: "file" | "directory"; ignored: boolean }>>(
      `/workspace/files?path=${encodeURIComponent(path)}`,
    ),
  workspaceContent: (path: string) =>
    apiFetch<{ type: "text" | "binary"; content: string; encoding?: "base64"; mimeType?: string }>(
      `/workspace/content?path=${encodeURIComponent(path)}`,
    ),
  downloadWorkspaceFile: (path: string) =>
    apiDownload(`/workspace/download?path=${encodeURIComponent(path)}`, path.split("/").at(-1) ?? "download"),
  downloadWorkspaceArchive: () => apiDownload("/workspace/archive", "workspace.zip"),
  deleteWorkspaceEntry: (path: string) =>
    apiFetch<{ deleted: string; type: "file" | "directory" }>("/workspace/files", {
      method: "DELETE",
      body: JSON.stringify({ path }),
    }),
}
