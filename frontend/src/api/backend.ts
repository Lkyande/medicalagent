import type { PermissionRequest, QuestionRequest } from "@opencode-ai/sdk/v2"
import { getToken } from "@/pages/chat/chat-client"
import { resolveBrowserUrl } from "@/utils/browser-url"

const BASE = resolveBrowserUrl(import.meta.env.VITE_BACKEND_API_URL ?? "/api")

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

function apiErrorMessage(text: string, fallback: string): string {
  if (!text) return fallback
  try {
    const payload = JSON.parse(text) as { detail?: unknown; message?: unknown }
    if (typeof payload.detail === "string" && payload.detail.trim()) return payload.detail
    if (typeof payload.message === "string" && payload.message.trim()) return payload.message
    if (Array.isArray(payload.detail)) {
      const messages = payload.detail
        .map((item) => item && typeof item === "object" && "msg" in item ? String(item.msg) : "")
        .filter(Boolean)
      if (messages.length) return messages.join("；")
    }
  } catch {}
  return text
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

export type WorkspaceUploadFile = {
  name: string
  path: string
  mime: string
  size: number
  url: string
  direct: boolean
}

export type EducationPreviewProgress = {
  jobId: string
  status: "running" | "completed" | "failed"
  stage: string
  label: string
  percent: number
  title?: string | null
  summary?: string | null
  editableHtmlUrl?: string | null
}

export type PendingInteractions = {
  permissions: PermissionRequest[]
  questions: QuestionRequest[]
}

export type WorkspaceHtmlArtifact = {
  path: string
  filename: string
  revision: string
  size: number
}

export type WorkspaceHtml = WorkspaceHtmlArtifact & {
  html: string
}

export type EducationPreviewArtifact = WorkspaceHtmlArtifact & {
  jobId: string
}

export type EducationPreviewHtml = EducationPreviewArtifact & {
  html: string
}

export type EducationPreviewImage = {
  url: string
  mime: string
  size: number
}

export type WechatAuthorization = {
  authorized: boolean
  authorizerAppid?: string | null
  authUrl?: string | null
  expiresAt?: string | null
}

export type EducationWechatRelease = {
  jobId: string
  status: "syncing" | "draft" | "publishing" | "submitted" | "unknown" | "failed"
  mediaId?: string | null
  contentRevision?: string | null
  publishId?: string | null
  authorizerAppid?: string | null
  updatedAt?: string | null
}

export async function apiFetch<T = unknown>(path: string, init?: RequestInit, timeoutMs = 15000): Promise<T> {
  const token = getToken()
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  let resp: Response
  const formData = typeof FormData !== "undefined" && init?.body instanceof FormData
  try {
    resp = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: "include",
      signal: init?.signal ?? controller.signal,
      headers: {
        ...(formData ? {} : { "Content-Type": "application/json" }),
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
    throw new ApiError(apiErrorMessage(text, resp.statusText || `HTTP ${resp.status}`), resp.status)
  }
  return resp.json() as Promise<T>
}

async function apiDownload(path: string, fallbackName: string): Promise<void> {
  const token = getToken()
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 120_000)
  let resp: Response
  try {
    resp = await fetch(`${BASE}${path}`, {
      credentials: "include",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw new ApiError("文件下载超时，请重试", 408)
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
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
  login: (account: string, password: string) =>
    apiFetch<{ token: string; user: { id: string; email: string | null; phone: string | null } }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ account, password }),
    }),
  register: (account: string, password: string) =>
    apiFetch<{ token: string; user: { id: string; email: string | null; phone: string | null } }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ account, password }),
    }),
  me: () => apiFetch<{ id: string; email: string | null; phone: string | null }>("/auth/me", undefined, 8000),
  dispatch: () => apiFetch<{ sandboxId: string; proxyBase: string; workspace: string }>("/dispatch", { method: "POST" }),
  assistants: () => apiFetch<PlatformAssistant[]>("/assistants"),
  enableAssistant: (id: string) =>
    apiFetch<AssistantEnableResult>(`/assistants/${encodeURIComponent(id)}/install`, { method: "POST" }),
  educationPreviewProgress: (sessionID: string, jobID: string) =>
    apiFetch<EducationPreviewProgress>(
      `/education/preview-progress/${encodeURIComponent(jobID)}?session_id=${encodeURIComponent(sessionID)}`,
      undefined,
      10_000,
    ),
  materializeEducationPreview: (sessionID: string, jobID: string) =>
    apiFetch<EducationPreviewArtifact>(
      `/education/previews/${encodeURIComponent(jobID)}/materialize?session_id=${encodeURIComponent(sessionID)}`,
      { method: "POST" },
      150_000,
    ),
  educationPreviewHtml: (sessionID: string, jobID: string) =>
    apiFetch<EducationPreviewHtml>(
      `/education/previews/${encodeURIComponent(jobID)}/html?session_id=${encodeURIComponent(sessionID)}`,
      undefined,
      30_000,
    ),
  saveEducationPreviewHtml: (sessionID: string, jobID: string, html: string, revision: string) =>
    apiFetch<EducationPreviewArtifact>(
      `/education/previews/${encodeURIComponent(jobID)}/html?session_id=${encodeURIComponent(sessionID)}`,
      { method: "PUT", body: JSON.stringify({ html, revision }) },
      180_000,
    ),
  uploadEducationPreviewImage: (sessionID: string, jobID: string, file: File) => {
    const form = new FormData()
    form.append("file", file, file.name)
    return apiFetch<EducationPreviewImage>(
      `/education/previews/${encodeURIComponent(jobID)}/images?session_id=${encodeURIComponent(sessionID)}`,
      { method: "POST", body: form },
      130_000,
    )
  },
  wechatAuthorization: () => apiFetch<WechatAuthorization>("/wechat/authorization", undefined, 15_000),
  createWechatAuthorization: () =>
    apiFetch<WechatAuthorization>("/wechat/authorization", { method: "POST" }, 30_000),
  educationWechatRelease: (sessionID: string, jobID: string) =>
    apiFetch<EducationWechatRelease | null>(
      `/wechat/previews/${encodeURIComponent(jobID)}/release?session_id=${encodeURIComponent(sessionID)}`,
      undefined,
      30_000,
    ),
  syncEducationWechatDraft: (sessionID: string, jobID: string, revision: string) =>
    apiFetch<EducationWechatRelease>(
      `/wechat/previews/${encodeURIComponent(jobID)}/draft?session_id=${encodeURIComponent(sessionID)}`,
      { method: "POST", body: JSON.stringify({ revision }) },
      180_000,
    ),
  publishEducationWechatDraft: (sessionID: string, jobID: string) =>
    apiFetch<EducationWechatRelease>(
      `/wechat/previews/${encodeURIComponent(jobID)}/publish?session_id=${encodeURIComponent(sessionID)}`,
      { method: "POST" },
      180_000,
    ),
  resolveEducationWechatPublish: (sessionID: string, jobID: string, published: boolean) =>
    apiFetch<EducationWechatRelease>(
      `/wechat/previews/${encodeURIComponent(jobID)}/resolve?session_id=${encodeURIComponent(sessionID)}`,
      { method: "POST", body: JSON.stringify({ published }) },
      30_000,
    ),
  pendingInteractions: () => apiFetch<PendingInteractions>("/interactions", undefined, 10_000),
  replyPermission: (requestID: string, reply: "once" | "always" | "reject") =>
    apiFetch<boolean>(`/interactions/permission/${encodeURIComponent(requestID)}/reply`, {
      method: "POST",
      body: JSON.stringify({ reply }),
    }),
  replyQuestion: (requestID: string, answers: string[][]) =>
    apiFetch<boolean>(`/interactions/question/${encodeURIComponent(requestID)}/reply`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    }),
  rejectQuestion: (requestID: string) =>
    apiFetch<boolean>(`/interactions/question/${encodeURIComponent(requestID)}/reject`, { method: "POST" }),
  workspace: () => apiFetch<{ id: string; sandboxId: string; directory: string }>("/workspace"),
  workspaceFiles: (path = "") =>
    apiFetch<Array<{ name: string; path: string; type: "file" | "directory"; ignored: boolean }>>(
      `/workspace/files?path=${encodeURIComponent(path)}`,
    ),
  workspaceContent: (path: string) =>
    apiFetch<{ type: "text" | "binary"; content: string; encoding?: "base64"; mimeType?: string }>(
      `/workspace/content?path=${encodeURIComponent(path)}`,
    ),
  workspaceHtml: (path: string) =>
    apiFetch<WorkspaceHtml>(`/workspace/html?path=${encodeURIComponent(path)}`, undefined, 30_000),
  saveWorkspaceHtml: (path: string, html: string, revision: string) =>
    apiFetch<WorkspaceHtmlArtifact>(
      `/workspace/html?path=${encodeURIComponent(path)}`,
      { method: "PUT", body: JSON.stringify({ html, revision }) },
      180_000,
    ),
  uploadWorkspaceFiles: (sessionID: string, files: File[]) => {
    const form = new FormData()
    form.append("session_id", sessionID)
    for (const file of files) form.append("files", file, file.name)
    return apiFetch<{ files: WorkspaceUploadFile[] }>("/workspace/uploads", {
      method: "POST",
      body: form,
    }, 300_000)
  },
  downloadWorkspaceFile: (path: string, filename?: string) =>
    apiDownload(
      `/workspace/download?path=${encodeURIComponent(path)}${filename ? `&filename=${encodeURIComponent(filename)}` : ""}`,
      filename ?? path.split("/").at(-1) ?? "download",
    ),
  downloadWorkspaceArchive: () => apiDownload("/workspace/archive", "workspace.zip"),
  deleteWorkspaceEntry: (path: string) =>
    apiFetch<{ deleted: string; type: "file" | "directory" }>("/workspace/files", {
      method: "DELETE",
      body: JSON.stringify({ path }),
    }),
}
