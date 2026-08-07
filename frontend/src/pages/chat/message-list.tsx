import { For, Show, createEffect, createMemo, createSignal, on, onCleanup, onMount } from "solid-js"
import { ChatMessageBubble } from "./message-bubble"
import type { DisplayMessage } from "./message-normalize"

export function ChatMessageList(props: {
  messages: DisplayMessage[]
  sessionID?: string
  generating?: boolean
  scrollToLatestRequest: number
  revertingMessageID?: string
  onRevert?: (messageID: string) => void
}) {
  let scrollRef: HTMLDivElement | undefined
  let contentRef: HTMLDivElement | undefined
  let atBottom = true
  let scrollFrame: number | undefined
  const [showLatest, setShowLatest] = createSignal(false)
  const waitingForAssistant = createMemo(
    () => !!props.generating && props.messages.at(-1)?.role === "user",
  )

  const handleScroll = () => {
    if (!scrollRef) return
    const distance = scrollRef.scrollHeight - scrollRef.scrollTop - scrollRef.clientHeight
    atBottom = distance < 80
    setShowLatest(!atBottom)
  }

  const scrollToBottom = () => {
    if (!scrollRef || !atBottom) return
    if (scrollFrame !== undefined) return
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = undefined
      if (scrollRef && atBottom) scrollRef.scrollTop = scrollRef.scrollHeight
    })
  }

  const jumpToLatest = () => {
    if (!scrollRef) return
    atBottom = true
    setShowLatest(false)
    scrollRef.scrollTo({ top: scrollRef.scrollHeight, behavior: "smooth" })
  }

  const forceScrollToBottom = () => {
    atBottom = true
    setShowLatest(false)
    scrollToBottom()
  }

  createEffect(on(
    () => [props.sessionID, props.scrollToLatestRequest] as const,
    () => forceScrollToBottom(),
  ))

  createEffect(() => {
    props.messages.map((m) =>
      m.blocks.reduce((size, block) => {
        if (block.type === "text" || block.type === "reasoning") return size + block.text.length
        if (block.type === "tool") return size + (block.tool.output?.length ?? 0) + (block.tool.error?.length ?? 0)
        return size + 1
      }, 0),
    )
    scrollToBottom()
  })

  onMount(() => {
    if (!contentRef) return
    const observer = new ResizeObserver(scrollToBottom)
    observer.observe(contentRef)
    onCleanup(() => observer.disconnect())
  })

  onCleanup(() => {
    if (scrollFrame !== undefined) cancelAnimationFrame(scrollFrame)
  })

  return (
    <div class="chat-messages-wrap">
      <div class="chat-messages" ref={scrollRef} onScroll={handleScroll}>
      <div class="chat-messages-inner" ref={contentRef}>
        <For each={props.messages}>
          {(msg) => (
            <ChatMessageBubble
              message={msg}
              sessionID={props.sessionID}
              reverting={props.revertingMessageID === msg.id}
              onRevert={props.onRevert}
            />
          )}
        </For>
        <Show when={waitingForAssistant()}>
          <div class="chat-message-row chat-message-row-assistant" aria-label="AI 正在回复" role="status">
            <div class="chat-bubble chat-bubble-assistant">
              <div class="chat-typing-indicator" aria-hidden="true">
                <div class="chat-typing-dot" />
                <div class="chat-typing-dot" />
                <div class="chat-typing-dot" />
              </div>
            </div>
          </div>
        </Show>
        <Show when={props.messages.length === 0}>
          <div class="chat-messages-placeholder">开始新的对话</div>
        </Show>
      </div>
      </div>
      <Show when={showLatest()}>
        <button class="chat-jump-latest" type="button" onClick={jumpToLatest} aria-label="回到最新消息">
          ↓ 回到最新
        </button>
      </Show>
    </div>
  )
}
