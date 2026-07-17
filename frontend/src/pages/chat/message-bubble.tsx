import { Show, For, Match, Switch, createEffect, createMemo, createSignal, onCleanup, type JSX } from "solid-js"
import { Markdown } from "@/session-ui/markdown"
import { backend, type EducationPreviewProgress } from "@/api/backend"
import { Progress } from "@/ui/components/progress"
import type { DisplayBlock, DisplayFile, DisplayMessage, DisplayTool } from "./message-normalize"

type ToolKind = "terminal" | "file" | "search" | "edit" | "task" | "checklist" | "question" | "skill" | "web" | "connector"

const toolLabels: Record<string, string> = {
  bash: "运行命令",
  read: "读取文件",
  list: "浏览目录",
  glob: "查找文件",
  grep: "搜索内容",
  edit: "编辑文件",
  write: "写入文件",
  apply_patch: "应用修改",
  task: "委派任务",
  todowrite: "更新任务",
  question: "请求确认",
  skill: "加载能力",
  webfetch: "读取网页",
  websearch: "搜索网页",
}

function toolKind(name: string): ToolKind {
  if (name === "bash") return "terminal"
  if (name === "read" || name === "list") return "file"
  if (name === "glob" || name === "grep") return "search"
  if (name === "edit" || name === "write" || name === "apply_patch") return "edit"
  if (name === "task") return "task"
  if (name === "todowrite") return "checklist"
  if (name === "question") return "question"
  if (name === "skill") return "skill"
  if (name.includes("web") || name.includes("browser") || name.includes("playwright")) return "web"
  return "connector"
}

function toolSummary(tool: DisplayTool): string {
  if (tool.title) return tool.title
  const input = tool.input as Record<string, unknown> | string | undefined
  if (typeof input === "string") return input.split("\n")[0] ?? ""
  if (!input || typeof input !== "object") return ""
  const fields = ["description", "command", "filePath", "path", "pattern", "query", "url", "name"]
  for (const field of fields) {
    const value = input[field]
    if (typeof value === "string" && value.trim()) return value.split("\n")[0] ?? ""
  }
  return ""
}

function ToolGlyph(props: { kind: ToolKind }): JSX.Element {
  const common = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8, "aria-hidden": true } as const
  switch (props.kind) {
    case "terminal": return <svg {...common}><rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7 9 3 3-3 3m5 0h5"/></svg>
    case "file": return <svg {...common}><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 12h6m-6 4h6"/></svg>
    case "search": return <svg {...common}><circle cx="11" cy="11" r="6"/><path d="m16 16 5 5M8 11h6"/></svg>
    case "edit": return <svg {...common}><path d="M4 20h4l11-11-4-4L4 16z"/><path d="m13.5 6.5 4 4"/></svg>
    case "task": return <svg {...common}><circle cx="6" cy="6" r="2"/><circle cx="18" cy="7" r="2"/><circle cx="12" cy="18" r="2"/><path d="m8 7 8 0m-9 1 4 8m6-7-4 7"/></svg>
    case "checklist": return <svg {...common}><path d="m4 7 2 2 3-4M11 7h9M4 15l2 2 3-4m2 2h9"/></svg>
    case "question": return <svg {...common}><path d="M21 12a8 8 0 0 1-8 8H6l-4 2 1.5-4A9 9 0 1 1 21 12Z"/><path d="M9.5 9a2.7 2.7 0 1 1 4.3 2.2c-1 .7-1.8 1.2-1.8 2.3m0 3h.01"/></svg>
    case "skill": return <svg {...common}><path d="m12 2 1.5 5.5L19 9l-5.5 1.5L12 16l-1.5-5.5L5 9l5.5-1.5z"/><path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg>
    case "web": return <svg {...common}><rect x="2" y="4" width="20" height="16" rx="3"/><path d="M2 9h20M6 6.5h.01M9 6.5h.01"/></svg>
    default: return <svg {...common}><path d="M8.5 14.5 6 17a3 3 0 0 1-4-4l4-4a3 3 0 0 1 4 0M15.5 9.5 18 7a3 3 0 1 1 4 4l-4 4a3 3 0 0 1-4 0M8 16l8-8"/></svg>
  }
}

function EducationPreviewProgressBar(props: { sessionID: string; jobID: string }) {
  const [progress, setProgress] = createSignal<EducationPreviewProgress>({
    jobId: props.jobID,
    status: "running",
    stage: "pending",
    label: "正在获取任务进度",
    percent: 5,
  })

  createEffect(() => {
    const sessionID = props.sessionID
    const jobID = props.jobID
    let active = true
    let loading = false
    let timer: number | undefined
    const refresh = async () => {
      if (loading) return
      loading = true
      try {
        const next = await backend.educationPreviewProgress(sessionID, jobID)
        if (!active) return
        setProgress((current) => ({
          ...next,
          percent: Math.max(current.percent, Math.min(100, Math.max(0, next.percent))),
        }))
        if (next.status !== "running" && timer !== undefined) {
          window.clearInterval(timer)
          timer = undefined
        }
      } catch {
        // Keep the last checkpoint visible during transient polling failures.
      } finally {
        loading = false
      }
    }
    void refresh()
    timer = window.setInterval(() => void refresh(), 4_000)
    onCleanup(() => {
      active = false
      if (timer !== undefined) window.clearInterval(timer)
    })
  })

  return (
    <div class="chat-education-progress" role="status" aria-live="polite">
      <Progress
        class="chat-education-progress-bar"
        value={progress().percent}
        minValue={0}
        maxValue={100}
        aria-label={progress().label}
      >
        <span class="chat-education-progress-copy">{progress().label}</span>
        <span class="chat-education-progress-value">{progress().percent}%</span>
      </Progress>
      <Show when={progress().status === "completed" && progress().editableHtmlUrl}>
        <div class="chat-education-preview-result">
          <Show when={progress().title}><strong>{progress().title}</strong></Show>
          <Show when={progress().summary}><p>{progress().summary}</p></Show>
          <a href={progress().editableHtmlUrl!} target="_blank" rel="noreferrer">打开完整预览</a>
        </div>
      </Show>
      <Show when={progress().status === "failed"}>
        <div class="chat-education-preview-failed">后台生成任务失败，请按任务编号联系管理员排查。</div>
      </Show>
    </div>
  )
}

function ToolCard(props: { tool: DisplayTool; sessionID?: string }) {
  const kind = createMemo(() => toolKind(props.tool.name))
  const label = createMemo(() => toolLabels[props.tool.name] ?? "调用工具")
  const summary = createMemo(() => toolSummary(props.tool))
  const statusLabel = createMemo(() => {
    if (props.tool.educationWaitTimedOut) return "后台生成中"
    if (props.tool.status === "completed") return "已完成"
    if (props.tool.status === "error") return "执行失败"
    if (props.tool.status === "running") return "执行中"
    return "等待中"
  })
  const visualStatus = createMemo(() => props.tool.educationWaitTimedOut ? "running" : props.tool.status)
  const inputText = createMemo(() => {
    const input = props.tool.input
    if (input === undefined || input === null) return ""
    if (typeof input === "string") return input
    try {
      return JSON.stringify(input, null, 2)
    } catch {
      return String(input)
    }
  })

  return (
    <details class="chat-tool-card" data-kind={kind()} data-status={visualStatus()} open={props.tool.status === "error" && !props.tool.educationWaitTimedOut}>
      <summary>
        <span class="chat-tool-glyph">
          <ToolGlyph kind={kind()} />
        </span>
        <span class="chat-tool-heading">
          <span class="chat-tool-name">{label()}</span>
          <Show when={summary()}><span class="chat-tool-title">{summary()}</span></Show>
        </span>
        <span class="chat-tool-status" data-status={visualStatus()}>
          <span class="chat-tool-status-dot" />
          <span>{statusLabel()}</span>
        </span>
        <svg class="chat-tool-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m9 18 6-6-6-6" /></svg>
      </summary>
      <Show when={(props.tool.status === "running" || props.tool.educationWaitTimedOut) && props.tool.educationJobID && props.sessionID}>
        <EducationPreviewProgressBar sessionID={props.sessionID!} jobID={props.tool.educationJobID!} />
      </Show>
      <div class="chat-tool-body">
        <Show when={inputText()}>
          <div class="chat-tool-label">输入</div>
          <pre class="chat-tool-pre">{inputText()}</pre>
        </Show>
        <Show when={props.tool.output}>
          <div class="chat-tool-label">结果</div>
          <pre class="chat-tool-pre">{props.tool.output}</pre>
        </Show>
        <Show when={props.tool.error && !props.tool.educationWaitTimedOut}>
          <div class="chat-tool-label chat-tool-label-error">错误</div>
          <pre class="chat-tool-pre chat-tool-pre-error">{props.tool.error}</pre>
        </Show>
      </div>
    </details>
  )
}

function FileBlock(props: { file: DisplayFile }) {
  const [downloading, setDownloading] = createSignal(false)
  const [error, setError] = createSignal("")
  const size = createMemo(() => {
    const bytes = props.file.size
    if (typeof bytes !== "number") return ""
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  })
  const download = async () => {
    const path = props.file.workspacePath
    if (!path || downloading()) return
    setDownloading(true)
    setError("")
    try {
      await backend.downloadWorkspaceFile(path, props.file.filename)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "文件下载失败")
    } finally {
      setDownloading(false)
    }
  }

  if (props.file.workspacePath) {
    return (
      <div class="chat-attachments">
        <button type="button" class="chat-file-card" onClick={download} disabled={downloading()}>
          <span class="chat-file-card-icon" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5"/><path d="M9 13h6m-6 4h4"/>
            </svg>
          </span>
          <span class="chat-file-card-copy">
            <strong>{props.file.filename ?? "附件"}</strong>
            <span>{size()}{size() ? " · " : ""}{props.file.direct ? "可供模型读取" : "工作区文件"}</span>
          </span>
          <span class="chat-file-card-action">{downloading() ? "下载中" : "下载"}</span>
        </button>
        <Show when={error()}><span class="chat-file-error" role="alert">{error()}</span></Show>
      </div>
    )
  }

  const safeUrl = /^(?:data:|blob:|https?:)/i.test(props.file.url)
  return (
    <div class="chat-attachments">
      <Show
        when={safeUrl && props.file.mime.startsWith("image/")}
        fallback={
          <Show when={safeUrl} fallback={<span class="chat-file-chip">{props.file.filename ?? "附件"}</span>}>
            <a class="chat-file-chip" href={props.file.url} target="_blank" rel="noreferrer">
              {props.file.filename ?? "附件"}
            </a>
          </Show>
        }
      >
        <img class="chat-attachment-img" src={props.file.url} alt={props.file.filename ?? ""} />
      </Show>
    </div>
  )
}

function MessageBlock(props: { block: DisplayBlock; streaming: boolean; sessionID?: string }) {
  return (
    <Switch>
      <Match when={props.block.type === "reasoning"}>
        <details class="chat-thinking">
          <summary>思考过程</summary>
          <div class="chat-thinking-body">
            <Markdown
              text={(props.block as Extract<DisplayBlock, { type: "reasoning" }>).text}
              cacheKey={props.block.id}
              streaming={props.streaming}
            />
          </div>
        </details>
      </Match>
      <Match when={props.block.type === "tool"}>
        <div class="chat-tools"><ToolCard tool={(props.block as Extract<DisplayBlock, { type: "tool" }>).tool} sessionID={props.sessionID} /></div>
      </Match>
      <Match when={props.block.type === "file"}>
        <FileBlock file={(props.block as Extract<DisplayBlock, { type: "file" }>).file} />
      </Match>
      <Match when={props.block.type === "text"}>
        <div class="chat-msg-content">
          <Markdown
            text={(props.block as Extract<DisplayBlock, { type: "text" }>).text}
            cacheKey={props.block.id}
            streaming={props.streaming}
          />
        </div>
      </Match>
    </Switch>
  )
}

function formatDuration(ms: number | undefined) {
  if (typeof ms !== "number" || ms < 0) return ""
  const total = Math.round(ms / 1000)
  if (total < 60) return `${total} 秒`
  return `${Math.floor(total / 60)} 分 ${total % 60} 秒`
}

function formatTokens(value: number) {
  return new Intl.NumberFormat("zh-CN", {
    notation: value >= 10000 ? "compact" : "standard",
    maximumFractionDigits: 1,
  }).format(value)
}

export function ChatMessageBubble(props: {
  message: DisplayMessage
  sessionID?: string
  reverting?: boolean
  onRevert?: (messageID: string) => void
}) {
  const isUser = createMemo(() => props.message.role === "user")
  const hasBody = createMemo(() => props.message.blocks.length > 0 || !!props.message.error)
  const showEmptyStreaming = createMemo(
    () => props.message.role === "assistant" && props.message.streaming && !hasBody(),
  )
  const metadata = createMemo(() => {
    if (isUser()) return ""
    const agent = props.message.agent
      ? props.message.agent[0]?.toUpperCase() + props.message.agent.slice(1)
      : ""
    const cost = props.message.cost && props.message.cost > 0
      ? `$${props.message.cost.toFixed(props.message.cost < 0.01 ? 4 : 2)}`
      : ""
    return [
      agent,
      props.message.model,
      formatDuration(props.message.durationMs),
      props.message.tokens ? `${formatTokens(props.message.tokens.total)} tokens` : "",
      cost,
    ].filter(Boolean).join(" · ")
  })
  const usageTitle = createMemo(() => {
    const tokens = props.message.tokens
    if (!tokens) return metadata()
    return `${metadata()}\n输入 ${tokens.input.toLocaleString()} · 输出 ${tokens.output.toLocaleString()} · 推理 ${tokens.reasoning.toLocaleString()} · 缓存读取 ${tokens.cacheRead.toLocaleString()} · 缓存写入 ${tokens.cacheWrite.toLocaleString()}`
  })
  const [copied, setCopied] = createSignal(false)

  const copyMessage = async () => {
    if (!props.message.text) return
    await navigator.clipboard.writeText(props.message.text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div
      class="chat-message-row"
      classList={{ "chat-message-row-user": isUser(), "chat-message-row-assistant": !isUser() }}
    >
      <div
        class="chat-bubble"
        classList={{ "chat-bubble-user": isUser(), "chat-bubble-assistant": !isUser() }}
        data-streaming={props.message.streaming ? "true" : undefined}
      >
        <For each={props.message.blocks}>
          {(block) => <MessageBlock block={block} streaming={props.message.streaming} sessionID={props.sessionID} />}
        </For>

        <Show when={showEmptyStreaming()}>
          <div class="chat-typing-indicator">
            <div class="chat-typing-dot" />
            <div class="chat-typing-dot" />
            <div class="chat-typing-dot" />
          </div>
        </Show>

        <Show when={props.message.error}>
          <div class="chat-error" role="alert">{props.message.error}</div>
        </Show>
      </div>
      <Show when={props.message.blocks.length > 0 && !props.message.streaming}>
        <div class="chat-message-actions">
          <Show when={isUser() && !props.message.optimistic && props.onRevert}>
            <button
              type="button"
              class="chat-action-btn"
              onClick={() => props.onRevert?.(props.message.id)}
              disabled={props.reverting}
              aria-label={props.reverting ? "正在撤销" : "撤销此消息及之后的内容"}
              title={props.reverting ? "正在撤销" : "撤销此消息及之后的内容"}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
                <path d="M9 7 4 12l5 5" />
                <path d="M5 12h8a6 6 0 0 1 6 6v1" />
              </svg>
            </button>
          </Show>
          <Show when={props.message.text}>
          <button
            type="button"
            class="chat-action-btn"
            onClick={copyMessage}
            aria-label={copied() ? "已复制" : "复制消息"}
            title={copied() ? "已复制" : "复制消息"}
          >
            <Show
              when={copied()}
              fallback={
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
                  <rect x="8" y="8" width="11" height="11" rx="2" />
                  <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
                </svg>
              }
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                <path d="m5 12 4 4L19 6" />
              </svg>
            </Show>
          </button>
          </Show>
          <Show when={metadata()}>
            <span class="chat-message-meta" title={usageTitle()}>{metadata()}</span>
          </Show>
        </div>
      </Show>
    </div>
  )
}
