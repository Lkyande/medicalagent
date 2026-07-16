import { createEffect, createSignal, Show, For, createMemo, onCleanup } from "solid-js"
import type { Message, Part } from "@opencode-ai/sdk/v2"
import { useNavigate } from "@solidjs/router"
import { getChatClient } from "./chat-client"
import { ChatModelSelector } from "./model-selector"
import { ChatAssistantSelector } from "./assistant-selector"
import { ChatStopButton } from "./stop-button"
import type { RawMessage } from "./message-normalize"
import { createChatIdentifier } from "./identifier"

const MAX_IMAGES = 5
const MAX_IMAGE_SIZE = 10 * 1024 * 1024

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
  onReloadSession: () => void
  onAbortComplete: () => Promise<void>
  onRestore?: () => void
}) {
  const navigate = useNavigate()
  const [text, setText] = createSignal("")
  const [images, setImages] = createSignal<File[]>([])
  const [sending, setSending] = createSignal(false)
  const [selectedModel, setSelectedModel] = createSignal<string>("")
  const [selectedAgent, setSelectedAgent] = createSignal("build")
  const [error, setError] = createSignal("")
  const [activeSessionID, setActiveSessionID] = createSignal<string>()
  const [stopping, setStopping] = createSignal(false)
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

  const canSend = createMemo(() => text().trim().length > 0 || images().length > 0)

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

  const handleImageUpload = (e: Event) => {
    const target = e.target as HTMLInputElement
    if (target.files) {
      const selected = Array.from(target.files)
      const invalid = selected.find((file) => !file.type.startsWith("image/"))
      const oversized = selected.find((file) => file.size > MAX_IMAGE_SIZE)
      if (invalid) {
        setError(`仅支持图片文件：${invalid.name}`)
      } else if (oversized) {
        setError(`图片不能超过 10 MB：${oversized.name}`)
      } else if (images().length + selected.length > MAX_IMAGES) {
        setError(`最多上传 ${MAX_IMAGES} 张图片`)
      } else {
        setError("")
        setImages((prev) => [...prev, ...selected])
      }
    }
    target.value = ""
  }

  const removeImage = (idx: number) => {
    setImages((prev) => prev.filter((_, i) => i !== idx))
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
    if (!selectedAgent()) {
      setError("请选择 Build 或 Plan Agent")
      return
    }
    setError("")

    const client = getChatClient()
    const currentText = text().trim()
    const currentImages = images()
    let optimisticMessageID: string | undefined
    if (!currentText && images().length === 0) return

    setSending(true)

    try {
      let sessionID = props.sessionId
      let created = false

      if (!sessionID) {
        const createResp = await client.session.create()
        sessionID = (createResp as any).data?.id
        if (!sessionID) throw new Error("创建会话失败")
        setActiveSessionID(sessionID)
        created = true
        const title = currentText.length > 40 ? currentText.slice(0, 40) + "..." : currentText
        client.session.update({ sessionID, title } as any).catch(() => {})
        navigate(`/chat/${sessionID}`)
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
      for (const img of currentImages) {
        const base64 = await fileToBase64(img)
        const partID = createChatIdentifier("prt")
        promptParts.push({ id: partID, type: "file", mime: img.type, filename: img.name, url: base64 })
        optimisticParts.push({
          id: partID,
          sessionID,
          messageID: optimisticMessageID,
          type: "file",
          mime: img.type,
          filename: img.name,
          url: base64,
        } as Part)
      }

      const optimistic: RawMessage = {
        info: {
          id: optimisticMessageID,
          sessionID,
          role: "user",
          time: { created: now },
          agent: selectedAgent(),
          model: { providerID: "", modelID: "" },
        } as Message,
        parts: optimisticParts,
      }
      props.onSent(optimistic)

      setText("")
      setImages([])
      if (textareaRef) {
        textareaRef.value = ""
        autoResize(textareaRef)
      }

      await client.session.promptAsync({
        sessionID,
        messageID: optimisticMessageID,
        agent: selectedAgent(),
        model: resolveModel(),
        parts: promptParts,
      } as any)

      if (!created) {
        props.onReloadSession()
      }

      if (created) {
        props.onReloadSession()
      }
    } catch (e: any) {
      if (optimisticMessageID) props.onSendFailed(optimisticMessageID)
      setError(e?.message ?? "发送失败")
      setText(currentText)
      setImages(currentImages)
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

        <Show when={images().length > 0}>
          <div class="chat-attachments">
            <For each={images()}>
              {(img, idx) => (
                <div class="chat-attachment-preview">
                  <ImagePreview file={img} />
                  <button class="chat-attachment-remove" aria-label={`移除图片 ${img.name}`} onClick={() => removeImage(idx())}>
                    ×
                  </button>
                </div>
              )}
            </For>
          </div>
        </Show>

        <div class="chat-composer-box">
          <textarea
            class="chat-composer-input"
            placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
            aria-label="消息"
            onInput={handleInput}
            onKeyDown={handleKeyDown}
            ref={textareaRef}
          />

          <div class="chat-composer-toolbar">
            <div class="chat-composer-tools">
              <label class="chat-icon-btn" title="上传图片" aria-label="上传图片">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
                </svg>
                <input
                  type="file"
                  accept="image/*"
                  multiple
                  style={{ display: "none" }}
                  onChange={handleImageUpload}
                />
              </label>
              <ChatAssistantSelector selected={selectedAgent()} onSelect={setSelectedAgent} />
              <ChatModelSelector selected={selectedModel()} onSelect={setSelectedModel} />
            </div>

            <div class="chat-composer-actions">
              <Show
                when={props.isGenerating || sending()}
                fallback={
                  <button class="chat-send-btn" aria-label="发送消息" disabled={!canSend() || !selectedAgent()} onClick={handleSend}>
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

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

function ImagePreview(props: { file: File }) {
  const url = URL.createObjectURL(props.file)
  onCleanup(() => URL.revokeObjectURL(url))
  return <img src={url} alt={props.file.name} />
}
