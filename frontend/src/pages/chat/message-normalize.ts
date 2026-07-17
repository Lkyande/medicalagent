import type { AssistantMessage, Message, Part, ToolPart, UserMessage } from "@opencode-ai/sdk/v2"
import { parseWorkspaceAttachment } from "./workspace-attachment"

export interface RawMessage {
  info: Message
  parts: Part[]
}

export interface DisplayTool {
  id: string
  name: string
  status: "pending" | "running" | "completed" | "error"
  input?: unknown
  output?: string
  error?: string
  title?: string
  educationJobID?: string
  educationWaitTimedOut?: boolean
}

export interface DisplayFile {
  id: string
  url: string
  mime: string
  filename?: string
  workspacePath?: string
  size?: number
  direct?: boolean
}

export type DisplayBlock =
  | { id: string; type: "text"; text: string }
  | { id: string; type: "reasoning"; text: string }
  | { id: string; type: "tool"; tool: DisplayTool }
  | { id: string; type: "file"; file: DisplayFile }

export interface DisplayMessage {
  id: string
  role: "user" | "assistant"
  blocks: DisplayBlock[]
  text: string
  time: number
  error?: string
  streaming: boolean
  optimistic?: boolean
  agent?: string
  model?: string
  durationMs?: number
  cost?: number
  tokens?: {
    total: number
    input: number
    output: number
    reasoning: number
    cacheRead: number
    cacheWrite: number
  }
}

function toolFromPart(part: ToolPart): DisplayTool {
  const state = part.state
  const base: DisplayTool = {
    id: part.id,
    name: part.tool,
    status: state.status,
    input: "input" in state ? state.input : undefined,
  }
  const toolInput = base.input
  if (
    part.tool.endsWith("education_wait_for_preview") &&
    toolInput &&
    typeof toolInput === "object" &&
    "job_id" in toolInput &&
    typeof toolInput.job_id === "string" &&
    /^[A-Za-z0-9_-]{1,80}$/.test(toolInput.job_id)
  ) {
    base.educationJobID = toolInput.job_id
  }
  if (state.status === "completed") {
    base.output = state.output
    base.title = state.title
  } else if (state.status === "error") {
    base.error = state.error
  } else if (state.status === "running") {
    base.title = state.title
  }
  if (
    base.educationJobID &&
    state.status === "error" &&
    /(?:MCP error -32001|Request timed out|operation timed out)/i.test(state.error)
  ) {
    base.educationWaitTimedOut = true
  }
  return base
}

function assistantError(info: Message): string | undefined {
  if (info.role !== "assistant") return undefined
  const err = info.error
  if (!err) return undefined
  const data = (err as { data?: { message?: string } }).data
  return data?.message ?? (err as { name?: string }).name ?? "发生错误"
}

function normalizeBlocks(parts: Part[]): DisplayBlock[] {
  const blocks: DisplayBlock[] = []
  const workspaceAttachments = new Map<string, NonNullable<ReturnType<typeof parseWorkspaceAttachment>>>()
  const linkedFileParts = new Set<string>()
  for (const part of parts ?? []) {
    if (part.type !== "text") continue
    const attachment = parseWorkspaceAttachment(part.text)
    if (!attachment) continue
    workspaceAttachments.set(part.id, attachment)
    if (attachment.filePartID) linkedFileParts.add(attachment.filePartID)
  }
  for (const part of parts ?? []) {
    switch (part.type) {
      case "text": {
        const attachment = workspaceAttachments.get(part.id)
        if (attachment) {
          blocks.push({
            id: part.id,
            type: "file",
            file: {
              id: part.id,
              url: "",
              mime: attachment.mime,
              filename: attachment.name,
              workspacePath: attachment.path,
              size: attachment.size,
              direct: attachment.direct,
            },
          })
          break
        }
        if (!part.synthetic && !part.ignored && part.text) blocks.push({ id: part.id, type: "text", text: part.text })
        break
      }
      case "reasoning":
        if (part.text) blocks.push({ id: part.id, type: "reasoning", text: part.text })
        break
      case "tool":
        blocks.push({ id: part.id, type: "tool", tool: toolFromPart(part) })
        break
      case "file":
        if (linkedFileParts.has(part.id)) break
        blocks.push({
          id: part.id,
          type: "file",
          file: { id: part.id, url: part.url, mime: part.mime, filename: part.filename },
        })
        break
    }
  }
  return blocks
}

function visibleText(blocks: DisplayBlock[]) {
  return blocks
    .filter((block): block is Extract<DisplayBlock, { type: "text" }> => block.type === "text")
    .map((block) => block.text)
    .join("\n\n")
}

function normalizeTokens(messages: AssistantMessage[]): DisplayMessage["tokens"] {
  if (messages.length === 0) return undefined
  return messages.reduce(
    (total, message) => ({
      total: total.total + (message.tokens.total ?? message.tokens.input + message.tokens.output + message.tokens.reasoning),
      input: total.input + message.tokens.input,
      output: total.output + message.tokens.output,
      reasoning: total.reasoning + message.tokens.reasoning,
      cacheRead: total.cacheRead + message.tokens.cache.read,
      cacheWrite: total.cacheWrite + message.tokens.cache.write,
    }),
    { total: 0, input: 0, output: 0, reasoning: 0, cacheRead: 0, cacheWrite: 0 },
  )
}

export function normalizeMessage(raw: RawMessage): DisplayMessage {
  const info = raw.info
  const blocks = normalizeBlocks(raw.parts)

  const completed = info.role === "assistant" ? typeof info.time.completed === "number" : true
  const assistant = info.role === "assistant" ? info : undefined

  return {
    id: info.id,
    role: info.role,
    blocks,
    text: visibleText(blocks),
    time: info.time.created,
    error: assistantError(info),
    streaming: info.role === "assistant" && !completed,
    optimistic: info.role === "user" && info.model.providerID === "",
    agent: assistant?.agent,
    model: assistant?.modelID,
    durationMs: assistant?.time.completed ? assistant.time.completed - assistant.time.created : undefined,
    cost: assistant?.cost,
    tokens: assistant ? normalizeTokens([assistant]) : undefined,
  }
}

function normalizeAssistantTurn(
  parentID: string,
  children: RawMessage[],
  user: UserMessage | undefined,
  active: boolean,
): DisplayMessage {
  const sorted = sortRawMessages(children)
  const blocks = sorted.flatMap((child) => normalizeBlocks(child.parts))
  const assistant = sorted.map((child) => child.info).filter((info): info is AssistantMessage => info.role === "assistant")
  const metadata = [...sorted].reverse().find((child) => visibleText(normalizeBlocks(child.parts)).trim())?.info ?? sorted.at(-1)?.info
  const completedAt = assistant.reduce<number | undefined>((latest, info) => {
    const completed = info.time.completed
    if (typeof completed !== "number") return latest
    return latest === undefined ? completed : Math.max(latest, completed)
  }, undefined)
  const durationMs =
    user && typeof completedAt === "number" && completedAt >= user.time.created
      ? completedAt - user.time.created
      : undefined
  const errors = assistant.map(assistantError).filter((error): error is string => !!error)

  return {
    id: `assistant-turn:${parentID}`,
    role: "assistant",
    blocks,
    text: visibleText(blocks),
    time: user?.time.created ?? assistant[0]?.time.created ?? 0,
    error: errors.at(-1),
    streaming: active,
    optimistic: false,
    agent: metadata?.role === "assistant" ? metadata.agent : undefined,
    model: metadata?.role === "assistant" ? metadata.modelID : undefined,
    durationMs,
    cost: assistant.reduce((total, info) => total + info.cost, 0),
    tokens: normalizeTokens(assistant),
  }
}

export function normalizeMessages(list: RawMessage[], sessionBusy = false): DisplayMessage[] {
  const sorted = sortRawMessages(list)
  const users = new Map<string, UserMessage>()
  const assistants = new Map<string, RawMessage[]>()

  for (const raw of sorted) {
    if (raw.info.role === "user") {
      users.set(raw.info.id, raw.info)
      continue
    }
    const group = assistants.get(raw.info.parentID)
    if (group) group.push(raw)
    else assistants.set(raw.info.parentID, [raw])
  }

  const latestUserID = [...users.values()].at(-1)?.id
  const emitted = new Set<string>()
  const result: DisplayMessage[] = []

  for (const raw of sorted) {
    if (raw.info.role === "user") {
      result.push(normalizeMessage(raw))
      const children = assistants.get(raw.info.id)
      if (children?.length) {
        result.push(normalizeAssistantTurn(raw.info.id, children, raw.info, sessionBusy && raw.info.id === latestUserID))
        emitted.add(raw.info.id)
      }
      continue
    }

    if (emitted.has(raw.info.parentID) || users.has(raw.info.parentID)) continue
    const children = assistants.get(raw.info.parentID)
    if (!children?.length) continue
    result.push(normalizeAssistantTurn(raw.info.parentID, children, undefined, sessionBusy && raw.info.parentID === latestUserID))
    emitted.add(raw.info.parentID)
  }

  return result
}

export function filterRevertedMessages(list: RawMessage[], revertMessageID?: string): RawMessage[] {
  if (!revertMessageID) return list
  return list.filter((message) =>
    message.info.role === "user"
      ? message.info.id < revertMessageID
      : message.info.parentID < revertMessageID,
  )
}

export function sortRawMessages(list: RawMessage[]): RawMessage[] {
  return [...list].sort((a, b) => {
    const ta = a.info?.time?.created ?? 0
    const tb = b.info?.time?.created ?? 0
    if (ta !== tb) return ta - tb
    return (a.info?.id ?? "").localeCompare(b.info?.id ?? "")
  })
}

function rawMessageText(message: RawMessage) {
  return message.parts
    .filter((part): part is Extract<Part, { type: "text" }> => part.type === "text")
    .map((part) => part.text)
    .join("\n\n")
}

function isOptimisticMessage(message: RawMessage) {
  return message.info.role === "user" && message.info.model.providerID === ""
}

function mergeSnapshotPart(snapshot: Part, live: Part): Part {
  if (snapshot.type !== live.type) return live
  if ((snapshot.type === "text" && live.type === "text") || (snapshot.type === "reasoning" && live.type === "reasoning")) {
    return live.text.length >= snapshot.text.length ? live : snapshot
  }
  if (snapshot.type === "tool" && live.type === "tool") {
    const rank = { pending: 0, running: 1, completed: 2, error: 2 }
    return rank[live.state.status] >= rank[snapshot.state.status] ? live : snapshot
  }
  return live
}

/** Merge a response snapshot without allowing newer SSE state to move backwards. */
export function mergeMessageSnapshot(snapshot: RawMessage[], current: RawMessage[]): RawMessage[] {
  const currentByID = new Map(current.map((message) => [message.info.id, message]))
  const confirmedUsers = snapshot.filter((message) => message.info.role === "user")
  const merged = snapshot.map((message) => {
    const live = currentByID.get(message.info.id)
    if (!live) return message
    currentByID.delete(message.info.id)
    const parts = new Map(message.parts.map((part) => [part.id, part]))
    for (const part of live.parts) {
      const snapshotPart = parts.get(part.id)
      parts.set(part.id, snapshotPart ? mergeSnapshotPart(snapshotPart, part) : part)
    }
    const liveCompleted = live.info.role === "assistant" && typeof live.info.time.completed === "number"
    const snapshotCompleted = message.info.role === "assistant" && typeof message.info.time.completed === "number"
    return { info: snapshotCompleted && !liveCompleted ? message.info : live.info, parts: [...parts.values()] }
  })

  for (const message of currentByID.values()) {
    if (isOptimisticMessage(message)) {
      const text = rawMessageText(message)
      const confirmed = confirmedUsers.some((candidate) =>
        rawMessageText(candidate) === text &&
        Math.abs(candidate.info.time.created - message.info.time.created) < 120_000
      )
      if (confirmed) continue
    }
    merged.push(message)
  }
  return sortRawMessages(merged)
}

export function upsertMessage(list: RawMessage[], info: Message): RawMessage[] {
  const idx = list.findIndex((m) => m.info.id === info.id)
  if (idx >= 0) {
    const next = [...list]
    next[idx] = { ...next[idx], info }
    return next
  }
  return [...list, { info, parts: [] }]
}

export function upsertPart(list: RawMessage[], part: Part): RawMessage[] {
  const idx = list.findIndex((m) => m.info.id === part.messageID)
  if (idx < 0) return list
  const message = list[idx]
  const partIdx = message.parts.findIndex((p) => p.id === part.id)
  const parts =
    partIdx >= 0
      ? message.parts.map((p, i) => (i === partIdx ? part : p))
      : [...message.parts, part]
  const next = [...list]
  next[idx] = { ...message, parts }
  return next
}

export function appendPartDelta(
  list: RawMessage[],
  messageID: string,
  partID: string,
  field: string,
  delta: string,
): RawMessage[] {
  const messageIndex = list.findIndex((message) => message.info.id === messageID)
  if (messageIndex < 0) return list
  const message = list[messageIndex]
  const partIndex = message.parts.findIndex((part) => part.id === partID)
  if (partIndex < 0) return list
  const part = message.parts[partIndex] as Part & Record<string, unknown>
  const current = part[field]
  if (typeof current !== "string") return list
  const parts = [...message.parts]
  parts[partIndex] = { ...part, [field]: current + delta } as Part
  const next = [...list]
  next[messageIndex] = { ...message, parts }
  return next
}

export function removePart(list: RawMessage[], messageID: string, partID: string): RawMessage[] {
  const idx = list.findIndex((m) => m.info.id === messageID)
  if (idx < 0) return list
  const message = list[idx]
  const parts = message.parts.filter((p) => p.id !== partID)
  const next = [...list]
  next[idx] = { ...message, parts }
  return next
}

export function removeMessage(list: RawMessage[], messageID: string): RawMessage[] {
  return list.filter((m) => m.info.id !== messageID)
}
