export type WorkspaceAttachment = {
  name: string
  path: string
  mime: string
  size: number
  direct: boolean
  filePartID?: string
  publicUrl?: string
  drawingSessionId?: string
}

const OPEN = "<workspace_attachment>"
const CLOSE = "</workspace_attachment>"

function validPath(path: string) {
  if (!path.startsWith("attachments/") || path.includes("\\") || path.includes("\0")) return false
  return !path.split("/").some((part) => !part || part === "." || part === "..")
}

export function serializeWorkspaceAttachment(attachment: WorkspaceAttachment): string {
  return `${OPEN}${JSON.stringify({
    name: attachment.name,
    path: attachment.path,
    mime: attachment.mime,
    size: attachment.size,
    direct: attachment.direct,
    filePartID: attachment.filePartID,
    publicUrl: attachment.publicUrl,
    drawingSessionId: attachment.drawingSessionId,
    instruction: attachment.publicUrl
      ? "This image is attached from the workspace. Use publicUrl only when the user explicitly requests image-to-video generation."
      : attachment.direct
      ? "This file is attached from the workspace."
      : "This opaque file is stored in the workspace. Use workspace tools to inspect or convert it; do not execute it automatically.",
  })}${CLOSE}`
}

export function parseWorkspaceAttachment(value: string): WorkspaceAttachment | undefined {
  const text = value.trim()
  if (!text.startsWith(OPEN) || !text.endsWith(CLOSE)) return undefined
  try {
    const parsed = JSON.parse(text.slice(OPEN.length, -CLOSE.length)) as Record<string, unknown>
    if (
      typeof parsed.name !== "string" || !parsed.name ||
      typeof parsed.path !== "string" || !validPath(parsed.path) ||
      typeof parsed.mime !== "string" || !parsed.mime.includes("/") ||
      typeof parsed.size !== "number" || !Number.isSafeInteger(parsed.size) || parsed.size < 0 ||
      typeof parsed.direct !== "boolean" ||
      (parsed.filePartID !== undefined && typeof parsed.filePartID !== "string") ||
      (parsed.publicUrl !== undefined && (typeof parsed.publicUrl !== "string" || !parsed.publicUrl.startsWith("https://"))) ||
      (parsed.drawingSessionId !== undefined && (
        typeof parsed.drawingSessionId !== "string" ||
        !/^drawing_[0-9a-f]{32}$/.test(parsed.drawingSessionId)
      ))
    ) return undefined
    return {
      name: parsed.name,
      path: parsed.path,
      mime: parsed.mime,
      size: parsed.size,
      direct: parsed.direct,
      filePartID: parsed.filePartID,
      publicUrl: parsed.publicUrl,
      drawingSessionId: parsed.drawingSessionId,
    }
  } catch {
    return undefined
  }
}
