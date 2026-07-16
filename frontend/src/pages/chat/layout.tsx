import { createEffect, createMemo, Show, createSignal, onMount, onCleanup } from "solid-js"
import { createStore, reconcile } from "solid-js/store"
import { useParams, useNavigate } from "@solidjs/router"
import type { Message, Part, Session, SessionStatus } from "@opencode-ai/sdk/v2"
import { ApiError, backend } from "@/api/backend"
import { getChatClient, getToken, setToken } from "./chat-client"
import { LoginPage } from "./login"
import { ChatSidebar, type SidebarSession } from "./sidebar"
import { ChatMessageList } from "./message-list"
import { ChatComposer } from "./composer"
import { ChatHeader } from "./header"
import { ChatEmptyState } from "./empty-state"
import { WorkspacePanel } from "./workspace-panel"
import { subscribeChatEvents } from "./chat-events"
import {
  appendPartDelta,
  filterRevertedMessages,
  mergeMessageSnapshot,
  normalizeMessages,
  removeMessage,
  removePart,
  upsertMessage,
  upsertPart,
  type DisplayMessage,
  type RawMessage,
} from "./message-normalize"

export function ChatLayout() {
  const [loggedIn, setLoggedIn] = createSignal<boolean | undefined>(undefined)

  onMount(async () => {
    if (!getToken()) {
      setLoggedIn(false)
      return
    }

    try {
      await backend.me()
      setLoggedIn(true)
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setToken(null)
      setLoggedIn(false)
    }
  })

  return (
    <Show when={loggedIn() !== undefined} fallback={<div class="chat-loading chat-loading-page">正在验证身份...</div>}>
      <Show when={loggedIn()} fallback={<LoginPage onLogin={() => setLoggedIn(true)} />}>
        <ChatWorkspace />
      </Show>
    </Show>
  )
}

function ChatWorkspace() {
  const params = useParams()
  const navigate = useNavigate()
  const sessionId = createMemo(() => params.id)

  const [sessions, setSessions] = createSignal<SidebarSession[]>([])
  const [sessionsLoading, setSessionsLoading] = createSignal(true)
  const [raw, setRaw] = createSignal<RawMessage[]>([])
  const [messages, setMessages] = createStore<DisplayMessage[]>([])
  const [messagesLoading, setMessagesLoading] = createSignal(false)
  const [sessionStatus, setSessionStatus] = createSignal<SessionStatus>({ type: "idle" })
  const [sidebarOpen, setSidebarOpen] = createSignal(false)
  const [sidebarCollapsed, setSidebarCollapsed] = createSignal(false)
  const [workspaceCollapsed, setWorkspaceCollapsed] = createSignal(false)
  const [workspaceOpen, setWorkspaceOpen] = createSignal(false)
  const [workspaceRefresh, setWorkspaceRefresh] = createSignal(0)
  const [workspaceMobile, setWorkspaceMobile] = createSignal(false)
  const [composerDraft, setComposerDraft] = createSignal("")
  const [sessionError, setSessionError] = createSignal("")
  const [currentSession, setCurrentSession] = createSignal<Session>()
  const [revertingMessageID, setRevertingMessageID] = createSignal<string>()
  const [localGeneratingMessageID, setLocalGeneratingMessageID] = createSignal<string>()

  onMount(() => {
    try {
      setSidebarCollapsed(localStorage.getItem("chat_sidebar_collapsed") === "true")
      setWorkspaceCollapsed(localStorage.getItem("chat_workspace_collapsed") === "true")
    } catch {}
    const media = window.matchMedia("(max-width: 960px)")
    const updateWorkspaceMode = () => setWorkspaceMobile(media.matches)
    updateWorkspaceMode()
    media.addEventListener("change", updateWorkspaceMode)
    onCleanup(() => media.removeEventListener("change", updateWorkspaceMode))
  })

  createEffect(() => {
    try {
      localStorage.setItem("chat_sidebar_collapsed", String(sidebarCollapsed()))
    } catch {}
  })

  createEffect(() => {
    try {
      localStorage.setItem("chat_workspace_collapsed", String(workspaceCollapsed()))
    } catch {}
  })

  createEffect(() => {
    const visible = filterRevertedMessages(raw(), currentSession()?.revert?.messageID)
    setMessages(reconcile(normalizeMessages(visible, sessionStatus().type !== "idle"), { key: "id" }))
  })

  const sessionInfo = createMemo(() => sessions().find((s) => s.id === sessionId()))
  const isGenerating = createMemo(() => sessionStatus().type !== "idle" || !!localGeneratingMessageID())
  const workspaceVisible = createMemo(() => workspaceMobile() ? workspaceOpen() : !workspaceCollapsed())

  const sessionTime = (info: Session) => info.time?.updated ?? info.time?.created ?? 0

  const loadSessions = async () => {
    try {
      const resp = await getChatClient().session.list()
      const data = (resp as any).data ?? []
      const list = data as Session[]
      const active = list.find((session) => session.id === sessionId())
      if (active) setCurrentSession(active)
      setSessions(
        list.map((s) => ({
          id: s.id,
          title: s.title || "新会话",
          time: s.time?.updated ?? s.time?.created ?? 0,
        })),
      )
    } catch (e) {
      console.error("Failed to load sessions", e)
    } finally {
      setSessionsLoading(false)
    }
  }

  let messageRequest = 0
  let eventRevision = 0
  const loadMessages = async () => {
    const id = sessionId()
    const request = ++messageRequest
    const revision = eventRevision
    if (!id) {
      setRaw([])
      setMessagesLoading(false)
      return
    }
    try {
      setMessagesLoading(true)
      const resp = await getChatClient().session.messages({ sessionID: id } as any)
      const data = (resp as any).data ?? []
      if (request !== messageRequest || id !== sessionId()) return
      const snapshot = Array.isArray(data) ? (data as RawMessage[]) : []
      setRaw((current) =>
        eventRevision !== revision || current.some((message) => message.info.role === "user" && message.info.model.providerID === "")
          ? mergeMessageSnapshot(snapshot, current)
          : snapshot,
      )
    } catch (e) {
      console.error("Failed to load messages", e)
      if (request !== messageRequest || id !== sessionId()) return
      if (eventRevision === revision) setRaw([])
    } finally {
      if (request === messageRequest) setMessagesLoading(false)
    }
  }

  const loadStatus = async () => {
    const id = sessionId()
    if (!id) {
      setSessionStatus({ type: "idle" })
      return
    }
    try {
      const resp = await getChatClient().session.status()
      const data = (resp as any).data ?? resp ?? {}
      const status = Array.isArray(data)
        ? data.find((item: any) => item?.sessionID === id || item?.id === id)?.status
        : data[id]
      setSessionStatus(status ?? { type: "idle" })
    } catch (error) {
      console.debug("Failed to load session status", error)
    }
  }

  const loadCurrentSession = async () => {
    const id = sessionId()
    if (!id) return
    try {
      const response = await getChatClient().session.get({ sessionID: id } as any)
      if (id !== sessionId()) return
      const session = (response as any).data as Session | undefined
      if (session) setCurrentSession(session)
    } catch (error) {
      console.debug("Failed to load session", error)
    }
  }

  const refreshAfterAbort = async () => {
    setLocalGeneratingMessageID(undefined)
    setSessionStatus({ type: "idle" })
    await loadMessages()
    await loadStatus()
  }

  let lastLoadedSession: string | undefined
  createEffect(() => {
    const id = sessionId()
    if (id === lastLoadedSession) return
    lastLoadedSession = id
    eventRevision++
    setRaw([])
    setCurrentSession(undefined)
    setSessionError("")
    setLocalGeneratingMessageID(undefined)
    setSessionStatus({ type: "idle" })
    loadMessages()
    loadStatus()
    loadCurrentSession()
  })

  const touchSession = (info: Session) => {
    setSessions((prev) => {
      const idx = prev.findIndex((s) => s.id === info.id)
      const next: SidebarSession = {
        id: info.id,
        title: info.title || "新会话",
        time: sessionTime(info),
      }
      if (idx >= 0) {
        const copy = [...prev]
        copy[idx] = next
        return copy
      }
      return [next, ...prev]
    })
  }

  const pendingParts = new Map<string, Map<string, Part>>()
  const pendingDeltas = new Map<string, Array<{ partID: string; field: string; delta: string }>>()

  const applyPendingEvents = (list: RawMessage[], messageID: string) => {
    let next = list
    const parts = pendingParts.get(messageID)
    if (parts) {
      for (const part of parts.values()) next = upsertPart(next, part)
      pendingParts.delete(messageID)
    }
    const deltas = pendingDeltas.get(messageID)
    if (deltas) {
      for (const item of deltas) next = appendPartDelta(next, messageID, item.partID, item.field, item.delta)
      pendingDeltas.delete(messageID)
    }
    return next
  }

  const applyPendingPartDeltas = (list: RawMessage[], messageID: string, partID: string) => {
    const deltas = pendingDeltas.get(messageID)
    if (!deltas) return list
    let next = list
    const remaining = []
    for (const item of deltas) {
      if (item.partID === partID) next = appendPartDelta(next, messageID, item.partID, item.field, item.delta)
      else remaining.push(item)
    }
    if (remaining.length) pendingDeltas.set(messageID, remaining)
    else pendingDeltas.delete(messageID)
    return next
  }

  onMount(() => {
    loadSessions()

    const unsubscribe = subscribeChatEvents({
      onMessageUpdated: (sid, info: Message) => {
        if (sid !== sessionId()) return
        eventRevision++
        setRaw((prev) => applyPendingEvents(upsertMessage(prev, info), info.id))
      },
      onMessageRemoved: (sid, messageID) => {
        if (sid !== sessionId()) return
        eventRevision++
        setRaw((prev) => removeMessage(prev, messageID))
      },
      onPartUpdated: (sid, part) => {
        if (sid !== sessionId()) return
        eventRevision++
        setRaw((prev) => {
          if (prev.some((message) => message.info.id === part.messageID)) {
            return applyPendingPartDeltas(upsertPart(prev, part), part.messageID, part.id)
          }
          const parts = pendingParts.get(part.messageID) ?? new Map<string, Part>()
          parts.set(part.id, part)
          pendingParts.set(part.messageID, parts)
          return prev
        })
        if (
          part.type === "tool" &&
          part.state.status === "completed" &&
          ["write", "edit", "apply_patch"].includes(part.tool)
        ) {
          setWorkspaceRefresh((value) => value + 1)
        }
      },
      onPartDelta: (sid, messageID, partID, field, delta) => {
        if (sid !== sessionId()) return
        eventRevision++
        setRaw((prev) => {
          const message = prev.find((item) => item.info.id === messageID)
          if (message?.parts.some((part) => part.id === partID)) {
            return appendPartDelta(prev, messageID, partID, field, delta)
          }
          const deltas = pendingDeltas.get(messageID) ?? []
          deltas.push({ partID, field, delta })
          pendingDeltas.set(messageID, deltas)
          return prev
        })
      },
      onPartRemoved: (sid, messageID, partID) => {
        if (sid !== sessionId()) return
        eventRevision++
        setRaw((prev) => removePart(prev, messageID, partID))
      },
      onSessionUpdated: (sid, info) => {
        touchSession(info)
        if (sid === sessionId()) setCurrentSession(info)
      },
      onSessionCreated: (_, info) => touchSession(info),
      onSessionDeleted: (sid) => {
        setSessions((prev) => prev.filter((s) => s.id !== sid))
        if (sid === sessionId()) {
          setLocalGeneratingMessageID(undefined)
          navigate("/chat")
        }
      },
      onFileEdited: () => setWorkspaceRefresh((value) => value + 1),
      onFileWatcherUpdated: () => setWorkspaceRefresh((value) => value + 1),
      onSessionStatus: (sid, status) => {
        if (sid !== sessionId()) return
        setSessionStatus(status)
        if (status.type !== "idle") setSessionError("")
        if (status.type === "idle") {
          setLocalGeneratingMessageID(undefined)
          setWorkspaceRefresh((value) => value + 1)
        }
      },
      onSessionError: (sid, error) => {
        if (sid && sid !== sessionId()) return
        const data = error && typeof error === "object" && "data" in error
          ? (error as { data?: { message?: string } }).data
          : undefined
        setSessionError(data?.message ?? (error instanceof Error ? error.message : "AI 回复失败"))
        setLocalGeneratingMessageID(undefined)
        setSessionStatus({ type: "idle" })
      },
      onError: (error) => console.debug("[chat] event stream error", error),
    })

    onCleanup(unsubscribe)
  })

  const handleSent = (optimistic: RawMessage) => {
    setSessionError("")
    setLocalGeneratingMessageID(optimistic.info.id)
    setRaw((prev) => [...prev, optimistic])
  }

  const handleSendFailed = (messageID: string) => {
    if (localGeneratingMessageID() === messageID) setLocalGeneratingMessageID(undefined)
    setRaw((prev) => removeMessage(prev, messageID))
  }

  const handleReloadSession = () => {
    loadMessages()
    loadSessions()
    loadStatus()
  }

  const revertedMessages = createMemo(() => {
    const boundary = currentSession()?.revert?.messageID
    if (!boundary) return []
    return raw().filter((message) => message.info.role === "user" && message.info.id >= boundary)
  })

  const handleRevert = async (messageID: string) => {
    const sid = sessionId()
    if (!sid || revertingMessageID()) return
    const target = raw().find((message) => message.info.id === messageID && message.info.role === "user")
    if (!target || target.info.model.providerID === "") return
    const previous = currentSession()
    setRevertingMessageID(messageID)
    setSessionError("")
    try {
      if (isGenerating()) await getChatClient().session.abort({ sessionID: sid } as any)
      setSessionStatus({ type: "idle" })
      setCurrentSession((session) => session ? { ...session, revert: { messageID } } : session)
      setComposerDraft(
        target.parts
          .filter((part): part is Extract<Part, { type: "text" }> => part.type === "text" && !part.synthetic)
          .map((part) => part.text)
          .join("\n\n"),
      )
      const response = await getChatClient().session.revert({ sessionID: sid, messageID } as any)
      const session = (response as any).data as Session | undefined
      if (session) setCurrentSession(session)
      setWorkspaceRefresh((value) => value + 1)
      await loadMessages()
    } catch (error: any) {
      setCurrentSession(previous)
      setSessionError(error?.message ?? "撤销对话失败")
    } finally {
      setRevertingMessageID(undefined)
    }
  }

  const handleUnrevert = async () => {
    const sid = sessionId()
    if (!sid || revertingMessageID()) return
    const previous = currentSession()
    setRevertingMessageID("restore")
    setSessionError("")
    try {
      const response = await getChatClient().session.unrevert({ sessionID: sid } as any)
      const session = (response as any).data as Session | undefined
      setCurrentSession(session ?? (previous ? { ...previous, revert: undefined } : previous))
      setComposerDraft("")
      setWorkspaceRefresh((value) => value + 1)
      await loadMessages()
    } catch (error: any) {
      setCurrentSession(previous)
      setSessionError(error?.message ?? "恢复对话失败")
    } finally {
      setRevertingMessageID(undefined)
    }
  }

  const handleDelete = async (sid: string) => {
    try {
      await getChatClient().session.delete({ sessionID: sid } as any)
      setSessions((prev) => prev.filter((s) => s.id !== sid))
      if (sid === sessionId()) navigate("/chat")
    } catch (e: any) {
      alert("删除失败: " + (e?.message ?? String(e)))
    }
  }

  return (
    <div
      class="chat-layout"
      classList={{ "sidebar-collapsed": sidebarCollapsed(), "workspace-collapsed": workspaceCollapsed() }}
      data-page="chat"
    >
      <ChatSidebar
        sessions={sessions()}
        loading={sessionsLoading()}
        currentSessionId={sessionId()}
        open={sidebarOpen()}
        collapsed={sidebarCollapsed()}
        onClose={() => setSidebarOpen(false)}
        onCollapse={() => setSidebarCollapsed(true)}
        onDelete={handleDelete}
      />
      <div class="chat-main">
        <ChatHeader
          title={sessionInfo()?.title || "新对话"}
          generating={isGenerating()}
          sidebarExpanded={!sidebarCollapsed()}
          workspaceExpanded={workspaceVisible()}
          onToggleSidebar={() => {
            if (window.matchMedia("(max-width: 768px)").matches) setSidebarOpen((v) => !v)
            else setSidebarCollapsed((value) => !value)
          }}
          onToggleWorkspace={() => {
            if (workspaceMobile()) setWorkspaceOpen((value) => !value)
            else setWorkspaceCollapsed((value) => !value)
          }}
        />
        <Show when={sessionId()} fallback={<ChatEmptyState />}>
          <Show
            when={!messagesLoading() || messages.length > 0}
            fallback={<div class="chat-loading">加载消息中...</div>}
          >
            <ChatMessageList
              messages={messages}
              generating={isGenerating()}
              revertingMessageID={revertingMessageID()}
              onRevert={handleRevert}
            />
          </Show>
        </Show>
        <ChatComposer
          sessionId={sessionId()}
          isGenerating={isGenerating()}
          draft={composerDraft()}
          externalError={sessionError()}
          revertedCount={revertedMessages().length}
          restoring={revertingMessageID() === "restore"}
          onRestore={handleUnrevert}
          onDraftConsumed={() => setComposerDraft("")}
          onSent={handleSent}
          onSendFailed={handleSendFailed}
          onReloadSession={handleReloadSession}
          onAbortComplete={refreshAfterAbort}
        />
      </div>
      <WorkspacePanel
        collapsed={workspaceCollapsed()}
        open={workspaceOpen()}
        refreshVersion={workspaceRefresh()}
        visible={workspaceVisible()}
        onClose={() => setWorkspaceOpen(false)}
        onCollapse={() => {
          setWorkspaceOpen(false)
          setWorkspaceCollapsed(true)
        }}
      />
    </div>
  )
}
