import { createEffect, createSignal, Show, For, createMemo, onCleanup } from "solid-js"
import type { Message, Part } from "@opencode-ai/sdk/v2"
import { useNavigate } from "@solidjs/router"
import { getChatClient } from "./chat-client"
import { backend, type AssistantEnableResult, type WorkspaceUploadFile } from "@/api/backend"
import { ChatModelSelector } from "./model-selector"
import { ChatAssistantSelector, CLINICAL_ASSISTANT_ID, DEFAULT_ASSISTANT_ID, type AssistantOption } from "./assistant-selector"
import { ChatStopButton } from "./stop-button"
import type { RawMessage } from "./message-normalize"
import { createChatIdentifier } from "./identifier"
import {
  CLINICAL_CONVERSATION_TYPE,
  GENERAL_CONVERSATION_TYPE,
  conversationMetadata,
} from "./conversation-type"
import { serializeWorkspaceAttachment } from "./workspace-attachment"

const MAX_ATTACHMENTS = 5
const MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
const MAX_ATTACHMENT_TOTAL_SIZE = 50 * 1024 * 1024
const PREVIEW_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"])

type ComposerAttachment = {
  file: File
  uploaded?: WorkspaceUploadFile
  uploadedSessionID?: string
}

export function ChatComposer(props: {
  sessionId?: string
  isGenerating: boolean
  draft: string
  externalError?: string
  revertedCount?: number
  restoring?: boolean
  onDraftConsumed: () => void
  onSent: (msg: RawMessage) => void
  onSendFailed: (messageID: string) => void
  onAbortComplete: () => Promise<void>
  onWorkspaceChanged: () => void
  onRestore?: () => void
  clinical?: boolean
  conversationTypeReady?: boolean
}) {
  const navigate = useNavigate()
  const [text, setText] = createSignal("")
  const [attachments, setAttachments] = createSignal<ComposerAttachment[]>([])
  const [sending, setSending] = createSignal(false)
  const [selectedModel, setSelectedModel] = createSignal<string>("")
  const [selectedAssistant, setSelectedAssistant] = createSignal<AssistantOption>({
    id: props.clinical ? CLINICAL_ASSISTANT_ID : DEFAULT_ASSISTANT_ID,
    name: props.clinical ? "诊疗助手" : "Build",
    description: props.clinical ? "基于已审核医学资料提供临床辅助决策建议。" : "基础执行 Agent，负责开发、修改和工具调用。",
    baseAgent: "build",
  })
  const [error, setError] = createSignal("")
  const [activeSessionID, setActiveSessionID] = createSignal<string>()
  const [stopping, setStopping] = createSignal(false)
  const [clinicalStatus, setClinicalStatus] = createSignal<"enabling" | "enabled" | "error">(
    props.clinical ? "enabling" : "enabled",
  )
  let textareaRef: HTMLTextAreaElement | undefined

  createEffect(() => {
    const draft = props.draft
    if (!draft) return
    setText(draft)
    queueMicrotask(() => {
      if (!textareaRef) return
      textareaRef.value = draft
      autoResize(textareaRef)
      textareaRef.focus()
    })
    props.onDraftConsumed()
  })

  createEffect(() => {
    if (props.externalError) setError(props.externalError)
  })

  createEffect(() => {
    if (!props.clinical) {
      setSelectedAssistant({
        id: DEFAULT_ASSISTANT_ID,
        name: "Build",
        description: "基础执行 Agent，负责开发、修改和工具调用。",
        baseAgent: "build",
      })
      setClinicalStatus("enabled")
      return
    }
    let cancelled = false
    onCleanup(() => {
      cancelled = true
    })
    setClinicalStatus("enabling")
    backend.enableAssistant(CLINICAL_ASSISTANT_ID).then((assistant: AssistantEnableResult) => {
      if (cancelled) return
      setSelectedAssistant({
        id: assistant.id,
        name: assistant.name,
        description: assistant.description,
        baseAgent: assistant.baseAgent,
        systemPrompt: assistant.systemPrompt,
        platform: assistant,
      })
      setClinicalStatus("enabled")
    }).catch((cause) => {
      if (cancelled) return
      setClinicalStatus("error")
      setError(cause instanceof Error ? cause.message : "诊疗助手启用失败")
    })
  })

  const canSend = createMemo(() => text().trim().length > 0 || attachments().length > 0)

  const selectAssistant = (assistant: AssistantOption) => {
    setSelectedAssistant(assistant)
  }

  const autoResize = (el: HTMLTextAreaElement) => {
    el.style.height = "auto"
    el.style.height = Math.min(el.scrollHeight, 200) + "px"
  }

  const handleInput = (e: InputEvent) => {
    const target = e.target as HTMLTextAreaElement
    setText(target.value)
    autoResize(target)
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.isComposing || e.keyCode === 229) return
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      if (props.isGenerating) {
        setError("当前回复仍在生成，可先编辑草稿或停止生成")
        return
      }
      handleSend()
    }
  }

  const handleFileUpload = (e: Event) => {
    const target = e.target as HTMLInputElement
    if (sending()) {
      target.value = ""
      return
    }
    if (target.files) {
      const selected = Array.from(target.files)
      const oversized = selected.find((file) => file.size > MAX_ATTACHMENT_SIZE)
      const totalSize = attachments().reduce((total, attachment) => total + attachment.file.size, 0)
      if (oversized) {
        setError(`文件不能超过 10 MB：${oversized.name}`)
      } else if (attachments().length + selected.length > MAX_ATTACHMENTS) {
        setError(`每条消息最多上传 ${MAX_ATTACHMENTS} 个文件`)
      } else if (totalSize + selected.reduce((total, file) => total + file.size, 0) > MAX_ATTACHMENT_TOTAL_SIZE) {
        setError("每条消息的附件总大小不能超过 50 MB")
      } else {
        setError("")
        setAttachments((prev) => [...prev, ...selected.map((file) => ({ file }))])
      }
    }
    target.value = ""
  }

  const removeAttachment = (idx: number) => {
    if (sending()) return
    setAttachments((prev) => prev.filter((_, i) => i !== idx))
  }

  const resolveModel = () => {
    const value = selectedModel()
    if (!value) return undefined
    const slash = value.indexOf("/")
    if (slash < 0) return undefined
    return { providerID: value.slice(0, slash), modelID: value.slice(slash + 1) }
  }

  const handleSend = async () => {
    if (!canSend() || sending() || props.isGenerating) return
    if (props.conversationTypeReady === false) {
      setError("正在识别会话类型，请稍候")
      return
    }
    if (props.clinical && clinicalStatus() !== "enabled") {
      setError(clinicalStatus() === "error" ? "诊疗助手启用失败，请刷新页面后重试" : "诊疗助手正在启用，请稍候")
      return
    }
    if (!selectedAssistant()) {
      setError("请选择助手")
      return
    }
    setError("")

    const client = getChatClient()
    const currentText = text().trim()
    const currentAttachments = attachments()
    let retryAttachments = currentAttachments
    let optimisticMessageID: string | undefined
    if (!currentText && currentAttachments.length === 0) return

    setSending(true)

    try {
      let sessionID = props.sessionId

      if (!sessionID) {
        const createResp = await client.session.create({
          metadata: conversationMetadata(
            props.clinical ? CLINICAL_CONVERSATION_TYPE : GENERAL_CONVERSATION_TYPE,
          ),
        })
        sessionID = (createResp as any).data?.id
        if (!sessionID) throw new Error("创建会话失败")
        setActiveSessionID(sessionID)
        const titleSource = currentText || currentAttachments[0]?.file.name || "文件分析"
        const title = titleSource.length > 40 ? titleSource.slice(0, 40) + "..." : titleSource
        client.session.update({ sessionID, title } as any).catch(() => {})
        navigate(`${props.clinical ? "/clinical" : "/chat"}/${sessionID}`)
      }

      const pendingIndexes = currentAttachments
        .map((attachment, index) => attachment.uploaded && attachment.uploadedSessionID === sessionID ? -1 : index)
        .filter((index) => index >= 0)
      if (pendingIndexes.length > 0) {
        const uploaded = await backend.uploadWorkspaceFiles(
          sessionID,
          pendingIndexes.map((index) => currentAttachments[index].file),
        )
        if (uploaded.files.length !== pendingIndexes.length) throw new Error("部分文件上传失败，请重试")
        retryAttachments = [...currentAttachments]
        pendingIndexes.forEach((attachmentIndex, resultIndex) => {
          retryAttachments[attachmentIndex] = {
            ...retryAttachments[attachmentIndex],
            uploaded: uploaded.files[resultIndex],
            uploadedSessionID: sessionID,
          }
        })
        setAttachments(retryAttachments)
        props.onWorkspaceChanged()
      }

      const now = Date.now()
      optimisticMessageID = createChatIdentifier("msg", now)
      const promptParts: any[] = []
      if (currentText) promptParts.push({ id: createChatIdentifier("prt"), type: "text", text: currentText })
      const optimisticParts: Part[] = []
      if (currentText) {
        optimisticParts.push({
          id: promptParts.at(-1).id,
          sessionID,
          messageID: optimisticMessageID,
          type: "text",
          text: currentText,
        } as Part)
      }
      for (const attachment of retryAttachments) {
        const uploaded = attachment.uploaded
        if (!uploaded || attachment.uploadedSessionID !== sessionID) throw new Error(`文件尚未上传：${attachment.file.name}`)
        const markerPartID = createChatIdentifier("prt")
        const filePartID = uploaded.direct ? createChatIdentifier("prt") : undefined
        const marker = serializeWorkspaceAttachment({
          name: uploaded.name,
          path: uploaded.path,
          mime: uploaded.mime,
          size: uploaded.size,
          direct: uploaded.direct,
          filePartID,
        })
        promptParts.push({ id: markerPartID, type: "text", text: marker })
        optimisticParts.push({
          id: markerPartID,
          sessionID,
          messageID: optimisticMessageID,
          type: "text",
          text: marker,
        } as Part)
        if (!filePartID) continue
        promptParts.push({ id: filePartID, type: "file", mime: uploaded.mime, filename: uploaded.name, url: uploaded.url })
        optimisticParts.push({
          id: filePartID,
          sessionID,
          messageID: optimisticMessageID,
          type: "file",
          mime: uploaded.mime,
          filename: uploaded.name,
          url: uploaded.url,
        } as Part)
      }

      const optimistic: RawMessage = {
        info: {
          id: optimisticMessageID,
          sessionID,
          role: "user",
          time: { created: now },
          agent: selectedAssistant().baseAgent,
          model: { providerID: "", modelID: "" },
        } as Message,
        parts: optimisticParts,
      }
      props.onSent(optimistic)

      setText("")
      setAttachments([])
      if (textareaRef) {
        textareaRef.value = ""
        autoResize(textareaRef)
      }

      await client.session.promptAsync({
        sessionID,
        messageID: optimisticMessageID,
        agent: selectedAssistant().baseAgent,
        system: selectedAssistant().systemPrompt,
        model: resolveModel(),
        parts: promptParts,
      } as any)
    } catch (e: any) {
      if (optimisticMessageID) props.onSendFailed(optimisticMessageID)
      setError(e?.message ?? "发送失败")
      setText(currentText)
      setAttachments(retryAttachments)
      if (textareaRef) {
        textareaRef.value = currentText
        autoResize(textareaRef)
      }
    } finally {
      setActiveSessionID(undefined)
      setSending(false)
    }
  }

  const handleStop = async () => {
    const sessionID = activeSessionID() ?? props.sessionId
    if (!sessionID || stopping()) return
    setStopping(true)
    setError("")
    try {
      await getChatClient().session.abort({ sessionID } as any)
      await props.onAbortComplete()
    } catch (e: any) {
      setError(e?.message ?? "停止生成失败")
    } finally {
      setStopping(false)
    }
  }

  return (
    <div class="chat-composer">
      <div class="chat-composer-inner">
        <Show when={(props.revertedCount ?? 0) > 0}>
          <div class="chat-revert-dock" role="status">
            <div>
              <strong>已撤销 {props.revertedCount} 条消息</strong>
              <span>发送新消息后，这些内容将被替换</span>
            </div>
            <button type="button" onClick={props.onRestore} disabled={props.restoring}>
              {props.restoring ? "正在恢复..." : "恢复全部"}
            </button>
          </div>
        </Show>
        <Show when={error()}>
          <div class="chat-error" role="alert">{error()}</div>
        </Show>

        <Show when={attachments().length > 0}>
          <div class="chat-attachments">
            <For each={attachments()}>
              {(attachment, idx) => (
                <div class="chat-attachment-preview" classList={{ "chat-attachment-preview-image": PREVIEW_IMAGE_TYPES.has(attachment.file.type) }}>
                  <AttachmentPreview attachment={attachment} />
                  <button
                    type="button"
                    class="chat-attachment-remove"
                    aria-label={`移除文件 ${attachment.file.name}`}
                    disabled={sending()}
                    onClick={() => removeAttachment(idx())}
                  >
                    ×
                  </button>
                </div>
              )}
            </For>
          </div>
        </Show>

        <div class="chat-composer-box" classList={{ "chat-composer-box-clinical": props.clinical }}>
          <Show when={props.conversationTypeReady === false}>
            <div class="chat-conversation-type-loading" role="status">正在识别会话类型...</div>
          </Show>
          <Show when={props.conversationTypeReady !== false && props.clinical}>
            <div class="chat-clinical-status" classList={{ error: clinicalStatus() === "error" }} role="status">
              <span class="chat-clinical-status-icon" aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 21s7-4.4 7-11V5l-7-2-7 2v5c0 6.6 7 11 7 11z"/><path d="M9 11h6M12 8v6"/></svg>
              </span>
              <strong>诊疗助手</strong>
              <span class="chat-clinical-status-dot" />
              <span>{clinicalStatus() === "enabling" ? "正在启用" : clinicalStatus() === "error" ? "启用失败" : "已启用"}</span>
              <span class="chat-clinical-status-detail">临床辅助决策</span>
            </div>
          </Show>
          <textarea
            class="chat-composer-input"
            placeholder={
              props.conversationTypeReady === false
                ? "正在加载会话..."
                : props.clinical
                  ? "描述症状、病史或检查结果..."
                  : "输入消息... (Enter 发送, Shift+Enter 换行)"
            }
            aria-label="消息"
            disabled={props.conversationTypeReady === false || sending()}
            onInput={handleInput}
            onKeyDown={handleKeyDown}
            ref={textareaRef}
          />

          <div class="chat-composer-toolbar">
            <div class="chat-composer-tools">
              <label
                class="chat-icon-btn"
                classList={{ disabled: sending() }}
                title="上传文件"
                aria-label="上传文件"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
                </svg>
                <input
                  type="file"
                  multiple
                  disabled={sending()}
                  style={{ display: "none" }}
                  onChange={handleFileUpload}
                />
              </label>
              <Show when={!props.clinical}>
                <ChatAssistantSelector selected={selectedAssistant().id} sessionId={props.sessionId} onSelect={selectAssistant} />
              </Show>
              <ChatModelSelector selected={selectedModel()} onSelect={setSelectedModel} />
            </div>

            <div class="chat-composer-actions">
              <Show
                when={props.isGenerating}
                fallback={
                  <button
                    class="chat-send-btn"
                    aria-label="发送消息"
                    disabled={!canSend() || props.conversationTypeReady === false || sending()}
                    onClick={handleSend}
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                      <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
                    </svg>
                  </button>
                }
              >
                <ChatStopButton onClick={handleStop} stopping={stopping()} />
              </Show>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function AttachmentPreview(props: { attachment: ComposerAttachment }) {
  const file = props.attachment.file
  if (!PREVIEW_IMAGE_TYPES.has(file.type)) {
    return (
      <div class="chat-pending-file">
        <span class="chat-pending-file-icon" aria-hidden="true">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5"/>
          </svg>
        </span>
        <span class="chat-pending-file-copy">
          <strong>{file.name}</strong>
          <span>{formatFileSize(file.size)}{props.attachment.uploaded ? " · 已上传" : ""}</span>
        </span>
      </div>
    )
  }
  const url = URL.createObjectURL(file)
  onCleanup(() => URL.revokeObjectURL(url))
  return <img src={url} alt={file.name} />
}
