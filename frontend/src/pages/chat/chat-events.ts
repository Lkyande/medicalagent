import type { GlobalEvent, Message, Part, Session, SessionStatus } from "@opencode-ai/sdk/v2"
import { batch } from "solid-js"
import { getChatClient } from "./chat-client"

type Payload = GlobalEvent["payload"]

export interface ChatEventHandlers {
  onMessageUpdated?: (sessionID: string, info: Message) => void
  onMessageRemoved?: (sessionID: string, messageID: string) => void
  onPartUpdated?: (sessionID: string, part: Part) => void
  onPartDelta?: (sessionID: string, messageID: string, partID: string, field: string, delta: string) => void
  onPartRemoved?: (sessionID: string, messageID: string, partID: string) => void
  onSessionUpdated?: (sessionID: string, info: Session) => void
  onSessionCreated?: (sessionID: string, info: Session) => void
  onSessionDeleted?: (sessionID: string, info: Session) => void
  onFileEdited?: (path: string) => void
  onFileWatcherUpdated?: (path: string, event: "add" | "change" | "unlink") => void
  onSessionStatus?: (sessionID: string, status: SessionStatus) => void
  onSessionError?: (sessionID: string | undefined, error: unknown) => void
  onError?: (error: unknown) => void
}

const RECONNECT_DELAY_MS = 2000

function dispatch(payload: Payload, handlers: ChatEventHandlers) {
  switch (payload.type) {
    case "message.updated":
      handlers.onMessageUpdated?.(payload.properties.sessionID, payload.properties.info)
      return
    case "message.removed":
      handlers.onMessageRemoved?.(payload.properties.sessionID, payload.properties.messageID)
      return
    case "message.part.updated":
      handlers.onPartUpdated?.(payload.properties.sessionID, payload.properties.part)
      return
    case "message.part.delta":
      handlers.onPartDelta?.(
        payload.properties.sessionID,
        payload.properties.messageID,
        payload.properties.partID,
        payload.properties.field,
        payload.properties.delta,
      )
      return
    case "message.part.removed":
      handlers.onPartRemoved?.(
        payload.properties.sessionID,
        payload.properties.messageID,
        payload.properties.partID,
      )
      return
    case "session.updated":
      handlers.onSessionUpdated?.(payload.properties.sessionID, payload.properties.info)
      return
    case "session.created":
      handlers.onSessionCreated?.(payload.properties.sessionID, payload.properties.info)
      return
    case "session.deleted":
      handlers.onSessionDeleted?.(payload.properties.sessionID, payload.properties.info)
      return
    case "file.edited":
      handlers.onFileEdited?.(payload.properties.file)
      return
    case "file.watcher.updated":
      handlers.onFileWatcherUpdated?.(payload.properties.file, payload.properties.event)
      return
    case "session.status":
      handlers.onSessionStatus?.(payload.properties.sessionID, payload.properties.status)
      return
    case "session.idle":
      handlers.onSessionStatus?.(payload.properties.sessionID, { type: "idle" })
      return
    case "session.error":
      handlers.onSessionError?.(payload.properties.sessionID, payload.properties.error)
      return
  }
}

/**
 * Subscribe to the OpenCode global SSE event stream via the FastAPI proxy.
 * Returns a cleanup function that stops the stream and reconnect loop.
 */
export function subscribeChatEvents(handlers: ChatEventHandlers): () => void {
  const abort = new AbortController()
  let stopped = false
  let queue: Payload[] = []
  let flushTimer: ReturnType<typeof setTimeout> | undefined

  const flush = () => {
    flushTimer = undefined
    if (stopped || queue.length === 0) return
    const events = queue
    queue = []
    const coalesced: Payload[] = []
    for (const payload of events) {
      const previous = coalesced.at(-1)
      if (
        payload.type === "message.part.delta" &&
        previous?.type === "message.part.delta" &&
        previous.properties.sessionID === payload.properties.sessionID &&
        previous.properties.messageID === payload.properties.messageID &&
        previous.properties.partID === payload.properties.partID &&
        previous.properties.field === payload.properties.field
      ) {
        previous.properties.delta += payload.properties.delta
      } else {
        coalesced.push(payload.type === "message.part.delta"
          ? { ...payload, properties: { ...payload.properties } }
          : payload)
      }
    }
    batch(() => coalesced.forEach((payload) => dispatch(payload, handlers)))
  }

  const enqueue = (payload: Payload) => {
    queue.push(payload)
    if (!flushTimer) flushTimer = setTimeout(flush, 16)
  }

  const run = async () => {
    while (!stopped && !abort.signal.aborted) {
      try {
        const events = await getChatClient().global.event({
          signal: abort.signal,
          onSseError: (error: unknown) => {
            if (abort.signal.aborted) return
            handlers.onError?.(error)
          },
        })
        for await (const event of events.stream as AsyncGenerator<GlobalEvent>) {
          if (stopped) break
          if (!event?.payload) continue
          enqueue(event.payload)
        }
      } catch (error) {
        if (abort.signal.aborted || stopped) return
        handlers.onError?.(error)
      }
      if (stopped || abort.signal.aborted) return
      await new Promise((resolve) => setTimeout(resolve, RECONNECT_DELAY_MS))
    }
  }

  void run()

  return () => {
    stopped = true
    if (flushTimer) clearTimeout(flushTimer)
    queue = []
    abort.abort()
  }
}
