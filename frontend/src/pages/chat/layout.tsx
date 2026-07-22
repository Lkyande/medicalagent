import { createEffect, createMemo, Show, createSignal, onMount, onCleanup } from "solid-js"
import { createStore, reconcile } from "solid-js/store"
import { useLocation, useParams, useNavigate } from "@solidjs/router"
import type { Message, Part, PermissionRequest, QuestionRequest, Session, SessionStatus } from "@opencode-ai/sdk/v2"
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
import { useDialog } from "@/ui/context/dialog"
import {
  CLINICAL_CONVERSATION_TYPE,
  EDUCATION_CONVERSATION_TYPE,
  GENERAL_CONVERSATION_TYPE,
  conversationHref,
  conversationMetadata,
  conversationTypeFromSession,
  detectLegacyConversationType,
  type ConversationType,
} from "./conversation-type"
import {
  appendPartDelta,
  educationJobIDs,
  filterRevertedMessages,
  mergeMessageSnapshot,
  latestEducationJobID,
  normalizeMessages,
  removeMessage,
  removePart,
  upsertMessage,
  upsertPart,
  type DisplayMessage,
  type RawMessage,
} from "./message-normalize"
import { parseWorkspaceAttachment } from "./workspace-attachment"
import { isAbortError } from "@/utils/server-errors"
import { ChatInteractionDock } from "./interaction-dock"
import { EducationPreviewCoordinator } from "./education-preview-coordinator"
import { setV2Toast, ToastRegion } from "@/utils/toast"

export function ChatLayout() {
  setV2Toast(false)
  const location = useLocation()
  const clinical = createMemo(() => location.pathname === "/clinical" || location.pathname.startsWith("/clinical/"))
  const education = createMemo(() => location.pathname === "/education" || location.pathname.startsWith("/education/"))
  const [loggedIn, setLoggedIn] = createSignal<boolean | undefined>(undefined)
  const [authError, setAuthError] = createSignal("")

  const verifyIdentity = async () => {
    if (!getToken()) {
      setLoggedIn(false)
      return
    }

    setLoggedIn(undefined)
    setAuthError("")
    try {
      await backend.me()
      setLoggedIn(true)
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setToken(null)
        setLoggedIn(false)
        return
      }
      setAuthError(error instanceof Error ? error.message : "身份验证失败")
      setLoggedIn(false)
    }
  }

  onMount(verifyIdentity)

  return (
    <>
      <ToastRegion v2={false} />
      <Show when={loggedIn() !== undefined} fallback={<div class="chat-loading chat-loading-page">正在验证身份...</div>}>
        <Show
          when={loggedIn()}
          fallback={
            <>
              <Show when={authError()}>
                <div class="chat-auth-error" role="alert">
                  <span>{authError()}</span>
                  <button type="button" onClick={verifyIdentity}>重试</button>
                </div>
              </Show>
              <LoginPage onLogin={() => setLoggedIn(true)} />
            </>
          }
        >
          <ChatWorkspace clinical={clinical()} education={education()} />
        </Show>
      </Show>
    </>
  )
}

function ChatWorkspace(props: { clinical?: boolean; education?: boolean }) {
  const params = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const dialog = useDialog()
  const sessionId = createMemo(() => params.id)

  const [sessions, setSessions] = createSignal<SidebarSession[]>([])
  const [sessionsLoading, setSessionsLoading] = createSignal(true)
  const [raw, setRaw] = createSignal<RawMessage[]>([])
  const [messages, setMessages] = createStore<DisplayMessage[]>([])
  const [messagesLoading, setMessagesLoading] = createSignal(false)
  const [sessionStatus, setSessionStatus] = createSignal<SessionStatus>({ type: "idle" })
  const [sidebarOpen, setSidebarOpen] = createSignal(false)
  const [sidebarCollapsed, setSidebarCollapsed] = createSignal(false)
  const [workspaceRefresh, setWorkspaceRefresh] = createSignal(0)
  const [composerDraft, setComposerDraft] = createSignal("")
  const [sessionError, setSessionError] = createSignal("")
  const [currentSession, setCurrentSession] = createSignal<Session>()
  const [revertingMessageID, setRevertingMessageID] = createSignal<string>()
  const [localGeneratingMessageID, setLocalGeneratingMessageID] = createSignal<string>()
  const [messagesLoadedSession, setMessagesLoadedSession] = createSignal<string>()
  const [permissions, setPermissions] = createSignal<PermissionRequest[]>([])
  const [questions, setQuestions] = createSignal<QuestionRequest[]>([])
  const [sessionStatusReady, setSessionStatusReady] = createSignal(false)

  onMount(() => {
    try {
      setSidebarCollapsed(localStorage.getItem("chat_sidebar_collapsed") === "true")
    } catch {}
  })

  createEffect(() => {
    try {
      localStorage.setItem("chat_sidebar_collapsed", String(sidebarCollapsed()))
    } catch {}
  })

  createEffect(() => {
    const visible = filterRevertedMessages(raw(), currentSession()?.revert?.messageID)
    setMessages(reconcile(normalizeMessages(visible, sessionStatus().type !== "idle"), { key: "id" }))
  })

  const sessionInfo = createMemo(() => sessions().find((s) => s.id === sessionId()))
  const persistedConversationType = createMemo<ConversationType | undefined>(() => {
    const current = conversationTypeFromSession(currentSession())
    return current ?? sessionInfo()?.conversationType
  })
  const detectedConversationType = createMemo<ConversationType | undefined>(() => {
    const id = sessionId()
    if (!id || messagesLoadedSession() !== id) return undefined
    return detectLegacyConversationType(
      raw(),
      props.clinical
        ? CLINICAL_CONVERSATION_TYPE
        : props.education
          ? EDUCATION_CONVERSATION_TYPE
          : GENERAL_CONVERSATION_TYPE,
    )
  })
  const conversationType = createMemo<ConversationType | undefined>(() => {
    if (!sessionId()) {
      return props.clinical
        ? CLINICAL_CONVERSATION_TYPE
        : props.education
          ? EDUCATION_CONVERSATION_TYPE
          : GENERAL_CONVERSATION_TYPE
    }
    return persistedConversationType() ?? detectedConversationType()
  })
  const conversationTypeReady = createMemo(() => !sessionId() || conversationType() !== undefined)
  const clinical = createMemo(
    () => conversationType() === CLINICAL_CONVERSATION_TYPE || (!conversationTypeReady() && !!props.clinical),
  )
  const education = createMemo(
    () => conversationType() === EDUCATION_CONVERSATION_TYPE || (!conversationTypeReady() && !!props.education),
  )
  const isGenerating = createMemo(() => sessionStatus().type !== "idle" || !!localGeneratingMessageID())
  const pendingPermission = createMemo(() => permissions().find((item) => item.sessionID === sessionId()))
  const pendingQuestion = createMemo(() => questions().find((item) => item.sessionID === sessionId()))
  const educationJobID = createMemo(() => latestEducationJobID(messages))
  const sessionEducationJobIDs = createMemo(() => educationJobIDs(messages))

  const loadInteractions = async () => {
    try {
      const pending = await backend.pendingInteractions()
      setPermissions(pending.permissions)
      setQuestions(pending.questions)
    } catch (error) {
      console.debug("Failed to load pending interactions", error)
    }
  }

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
          conversationType: conversationTypeFromSession(s),
        })),
      )
      void loadInteractions()
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
      setMessagesLoadedSession(undefined)
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
      setMessagesLoadedSession(id)
    } catch (e) {
      console.error("Failed to load messages", e)
      if (request !== messageRequest || id !== sessionId()) return
      if (eventRevision === revision) setRaw([])
      setSessionError("会话消息加载失败，暂时无法确认会话类型")
    } finally {
      if (request === messageRequest) setMessagesLoading(false)
    }
  }

  const loadStatus = async () => {
    const id = sessionId()
    if (!id) {
      setSessionStatus({ type: "idle" })
      setSessionStatusReady(true)
      return
    }
    try {
      const resp = await getChatClient().session.status()
      const data = (resp as any).data ?? resp ?? {}
      const status = Array.isArray(data)
        ? data.find((item: any) => item?.sessionID === id || item?.id === id)?.status
        : data[id]
      if (id !== sessionId()) return
      setSessionStatus(status ?? { type: "idle" })
      setSessionStatusReady(true)
      if (!status || status.type === "idle") setLocalGeneratingMessageID(undefined)
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
    setSessionStatusReady(false)
    await loadMessages()
    await loadStatus()
  }

  let lastLoadedSession: string | undefined
  let classificationRequest: string | undefined
  createEffect(() => {
    const id = sessionId()
    if (id === lastLoadedSession) return
    lastLoadedSession = id
    eventRevision++
    setRaw((current) => current.filter((message) => message.info.sessionID === id))
    setMessagesLoadedSession(undefined)
    classificationRequest = undefined
    setCurrentSession(undefined)
    setSessionError("")
    setLocalGeneratingMessageID(undefined)
    setSessionStatus({ type: "idle" })
    setSessionStatusReady(false)
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
        conversationType: conversationTypeFromSession(info),
      }
      if (idx >= 0) {
        const copy = [...prev]
        copy[idx] = next
        return copy
      }
      return [next, ...prev]
    })
  }

  createEffect(() => {
    const id = sessionId()
    const type = detectedConversationType()
    if (!id || !type || persistedConversationType()) return
    const requestKey = `${id}:${type}`
    if (classificationRequest === requestKey) return
    classificationRequest = requestKey
    const metadata = conversationMetadata(type, currentSession()?.metadata)
    getChatClient().session.update({ sessionID: id, metadata } as any).then((response: any) => {
      if (id !== sessionId()) return
      const updated = response?.data as Session | undefined
      if (updated) setCurrentSession(updated)
      else setCurrentSession((current) => current ? { ...current, metadata } : current)
      setSessions((current) =>
        current.map((session) => session.id === id ? { ...session, conversationType: type } : session),
      )
    }).catch((error: unknown) => {
      if (id !== sessionId()) return
      setSessionError(error instanceof Error ? error.message : "无法保存会话类型")
    })
  })

  createEffect(() => {
    const id = sessionId()
    const type = persistedConversationType()
    if (!id || !type) return
    const target = conversationHref(id, type)
    if (location.pathname !== target) navigate(target, { replace: true })
  })

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
    void loadInteractions()

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
          navigate(clinical() ? "/clinical" : education() ? "/education" : "/chat")
        }
      },
      onFileEdited: () => setWorkspaceRefresh((value) => value + 1),
      onFileWatcherUpdated: () => setWorkspaceRefresh((value) => value + 1),
      onSessionStatus: (sid, status) => {
        if (sid !== sessionId()) return
        setSessionStatusReady(true)
        setSessionStatus(status)
        if (status.type !== "idle") setSessionError("")
        if (status.type === "idle") {
          setLocalGeneratingMessageID(undefined)
          setWorkspaceRefresh((value) => value + 1)
          void loadMessages()
        }
      },
      onSessionError: (sid, error) => {
        if (sid && sid !== sessionId()) return
        if (isAbortError(error)) {
          setLocalGeneratingMessageID(undefined)
          setSessionStatus({ type: "idle" })
          return
        }
        const data = error && typeof error === "object" && "data" in error
          ? (error as { data?: { message?: string } }).data
          : undefined
        setSessionError(data?.message ?? (error instanceof Error ? error.message : "AI 回复失败"))
        setLocalGeneratingMessageID(undefined)
        setSessionStatus({ type: "idle" })
      },
      onPermissionAsked: (request) => {
        setPermissions((current) => [...current.filter((item) => item.id !== request.id), request])
      },
      onPermissionReplied: (_, requestID) => {
        setPermissions((current) => current.filter((item) => item.id !== requestID))
      },
      onQuestionAsked: (request) => {
        setQuestions((current) => [...current.filter((item) => item.id !== request.id), request])
      },
      onQuestionResolved: (_, requestID) => {
        setQuestions((current) => current.filter((item) => item.id !== requestID))
      },
      onConnected: () => {
        void loadInteractions()
        void loadMessages()
        void loadStatus()
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
          .filter(
            (part): part is Extract<Part, { type: "text" }> =>
              part.type === "text" && !part.synthetic && !parseWorkspaceAttachment(part.text),
          )
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
      if (sid === sessionId()) navigate(clinical() ? "/clinical" : education() ? "/education" : "/chat")
    } catch (e: any) {
      alert("删除失败: " + (e?.message ?? String(e)))
    }
  }

  const openWorkspace = (initialPath?: string) => {
    const sid = sessionId()
    void dialog.show(() => (
      <WorkspacePanel
        refreshVersion={workspaceRefresh()}
        sessionID={sid}
        educationJobIDs={education() ? sessionEducationJobIDs() : undefined}
        initialPath={initialPath}
        onWorkspaceChanged={() => setWorkspaceRefresh((value) => value + 1)}
      />
    ))
  }

  return (
    <div
      class="chat-layout"
      classList={{ "sidebar-collapsed": sidebarCollapsed() }}
      data-page="chat"
    >
      <ChatSidebar
        sessions={sessions()}
        loading={sessionsLoading()}
        currentSessionId={sessionId()}
        clinical={clinical()}
        education={education()}
        open={sidebarOpen()}
        collapsed={sidebarCollapsed()}
        onClose={() => setSidebarOpen(false)}
        onCollapse={() => setSidebarCollapsed(true)}
        onExpand={() => setSidebarCollapsed(false)}
        onDelete={handleDelete}
      />
      <div class="chat-main">
        <ChatHeader
          title={sessionInfo()?.title || (clinical() ? "诊疗助手" : education() ? "宣教助手" : "新对话")}
          generating={isGenerating()}
          sidebarExpanded={!sidebarCollapsed()}
          onToggleSidebar={() => {
            if (window.matchMedia("(max-width: 768px)").matches) setSidebarOpen((v) => !v)
            else setSidebarCollapsed((value) => !value)
          }}
          onOpenWorkspace={() => openWorkspace()}
        />
        <Show when={sessionId()} fallback={<ChatEmptyState clinical={clinical()} education={education()} />}>
          <Show
            when={!messagesLoading() || messages.length > 0}
            fallback={<div class="chat-loading">加载消息中...</div>}
          >
            <ChatMessageList
              messages={messages}
              sessionID={sessionId()}
              generating={isGenerating()}
              revertingMessageID={revertingMessageID()}
              onRevert={handleRevert}
            />
          </Show>
        </Show>
        <Show when={education() && sessionId() && educationJobID()}>
          <EducationPreviewCoordinator
            sessionID={sessionId()!}
            jobID={educationJobID()!}
            onWorkspaceChanged={() => setWorkspaceRefresh((value) => value + 1)}
            onOpenWorkspace={openWorkspace}
          />
        </Show>
        <ChatInteractionDock
          permission={pendingQuestion() ? undefined : pendingPermission()}
          question={pendingQuestion()}
          onResolved={(requestID) => {
            setPermissions((current) => current.filter((item) => item.id !== requestID))
            setQuestions((current) => current.filter((item) => item.id !== requestID))
          }}
        />
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
          onAbortComplete={refreshAfterAbort}
          onWorkspaceChanged={() => setWorkspaceRefresh((value) => value + 1)}
          clinical={clinical()}
          education={education()}
          conversationTypeReady={conversationTypeReady()}
          sessionStatusReady={sessionStatusReady()}
        />
      </div>
    </div>
  )
}
