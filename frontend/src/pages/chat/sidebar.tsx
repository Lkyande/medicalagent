import { createMemo, For, Show, createSignal, onCleanup, onMount } from "solid-js"
import { useNavigate, A } from "@solidjs/router"
import { setToken } from "./chat-client"
import { groupByTime, formatRelativeTime } from "./group-by-time"
import {
  CLINICAL_CONVERSATION_TYPE,
  EDUCATION_CONVERSATION_TYPE,
  GENERAL_CONVERSATION_TYPE,
  conversationHref,
  type ConversationType,
} from "./conversation-type"

export interface SidebarSession {
  id: string
  title: string
  time: number
  conversationType?: ConversationType
}

export function ChatSidebar(props: {
  sessions: SidebarSession[]
  loading: boolean
  currentSessionId?: string
  clinical?: boolean
  education?: boolean
  open: boolean
  collapsed: boolean
  onClose: () => void
  onCollapse: () => void
  onExpand: () => void
  onDelete: (id: string) => void
}) {
  const navigate = useNavigate()
  const [collapsedEarlier, setCollapsedEarlier] = createSignal(true)
  let sidebarRef: HTMLDivElement | undefined

  onMount(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && props.open) props.onClose()
    }
    document.addEventListener("keydown", closeOnEscape)
    onCleanup(() => document.removeEventListener("keydown", closeOnEscape))
  })

  const groups = createMemo(() =>
    groupByTime(
      props.sessions.map((s) => ({
        id: s.id,
        title: s.title,
        time: { updated: s.time },
        conversationType: s.conversationType,
      })),
    ),
  )

  const newChat = () => {
    props.onClose()
    navigate("/chat")
  }

  const openClinical = () => {
    props.onClose()
    navigate("/clinical")
  }

  const openEducation = () => {
    props.onClose()
    navigate("/education")
  }

  const handleLogout = () => {
    setToken(null)
    window.location.reload()
  }

  return (
    <>
      <Show when={props.open}>
        <div class="chat-sidebar-backdrop" onClick={props.onClose} />
      </Show>
      <aside
        class="chat-sidebar"
        classList={{ open: props.open, collapsed: props.collapsed }}
        aria-label="历史会话"
        ref={sidebarRef}
      >
        <div class="chat-sidebar-rail" aria-hidden={!props.collapsed}>
          <button class="chat-rail-brand" onClick={props.onExpand} aria-label="展开历史会话" title="展开历史会话">A</button>
          <button class="chat-rail-action" onClick={newChat} aria-label="新建对话" title="新建对话">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 5v14M5 12h14" /></svg>
          </button>
          <button class="chat-rail-action" classList={{ active: props.clinical }} onClick={openClinical} aria-label="诊疗助手" title="诊疗助手">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 21s7-4.4 7-11V5l-7-2-7 2v5c0 6.6 7 11 7 11z"/><path d="M9 11h6M12 8v6"/></svg>
          </button>
          <button class="chat-rail-action" classList={{ active: props.education }} onClick={openEducation} aria-label="宣教助手" title="宣教助手">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 6h16v12H4z"/><path d="M8 10h8M8 14h5"/><path d="M9 18v3M15 18v3"/></svg>
          </button>
          <span class="chat-rail-separator" aria-hidden="true" />
          <button class="chat-rail-action" onClick={props.onExpand} aria-label="查看历史会话" title="历史会话">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>
          </button>
          <button class="chat-rail-action chat-rail-logout" onClick={handleLogout} aria-label="退出登录" title="退出登录">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10 17l5-5-5-5M15 12H3"/><path d="M14 3h5a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-5"/></svg>
          </button>
        </div>
        <div class="chat-sidebar-expanded">
          <div class="chat-sidebar-header">
            <div class="chat-sidebar-brand"><span>M</span><strong>Medical Agent</strong></div>
            <button class="chat-sidebar-collapse" onClick={props.onCollapse} aria-label="收起历史会话" title="收起历史会话">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M15 18l-6-6 6-6" /></svg>
            </button>
          </div>
          <div class="chat-sidebar-new-wrap">
            <button class="chat-new-btn" onClick={newChat}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14" /></svg>
              新建对话
            </button>
            <button class="chat-clinical-entry" classList={{ active: props.clinical }} onClick={openClinical}>
              <span class="chat-clinical-entry-icon">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 21s7-4.4 7-11V5l-7-2-7 2v5c0 6.6 7 11 7 11z"/><path d="M9 11h6M12 8v6"/></svg>
              </span>
              <span>诊疗助手</span>
            </button>
            <button class="chat-clinical-entry" classList={{ active: props.education }} onClick={openEducation}>
              <span class="chat-clinical-entry-icon">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16v12H4z"/><path d="M8 10h8M8 14h5"/><path d="M9 18v3M15 18v3"/></svg>
              </span>
              <span>宣教助手</span>
            </button>
          </div>
          <div class="chat-history-heading">
            <span>历史对话</span>
            <span class="chat-history-heading-line" />
          </div>
          <div class="chat-session-list">
          <Show when={props.loading && props.sessions.length === 0}>
            <div class="chat-loading">加载中...</div>
          </Show>
          <Show when={!props.loading && props.sessions.length === 0}>
            <div class="chat-session-empty">暂无历史对话</div>
          </Show>
          <For each={groups()}>
            {(group) => {
              const isEarlier = group.label === "更早"
              return (
                <div>
                  <button
                    type="button"
                    class="chat-session-group-label"
                    classList={{ "chat-session-group-collapsible": isEarlier }}
                    onClick={() => isEarlier && setCollapsedEarlier(!collapsedEarlier())}
                    aria-expanded={isEarlier ? !collapsedEarlier() : undefined}
                    disabled={!isEarlier}
                  >
                    <Show when={isEarlier}>
                      <span class="chat-session-group-chevron">{collapsedEarlier() ? "▸" : "▾"}</span>
                    </Show>
                    {group.label}
                    <Show when={isEarlier}>
                      <span class="chat-session-group-count">({group.items.length})</span>
                    </Show>
                  </button>
                  <Show when={!isEarlier || !collapsedEarlier()}>
                    <For each={group.items}>
                      {(item) => (
                        <div class="chat-session-row">
                          <A
                            href={conversationHref(
                              item.id,
                              item.conversationType ?? GENERAL_CONVERSATION_TYPE,
                            )}
                            class="chat-session-item"
                            classList={{ active: item.id === props.currentSessionId }}
                            onClick={props.onClose}
                          >
                            <span class="chat-session-item-title">
                              <Show when={item.conversationType === CLINICAL_CONVERSATION_TYPE}>
                                <span class="chat-session-clinical-mark" title="诊疗助手对话" aria-label="诊疗助手对话">
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 21s7-4.4 7-11V5l-7-2-7 2v5c0 6.6 7 11 7 11z"/><path d="M9 11h6M12 8v6"/></svg>
                                </span>
                              </Show>
                              <Show when={item.conversationType === EDUCATION_CONVERSATION_TYPE}>
                                <span class="chat-session-clinical-mark" title="宣教助手对话" aria-label="宣教助手对话">
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M4 6h16v12H4z"/><path d="M8 10h8M8 14h5"/></svg>
                                </span>
                              </Show>
                              <span>{item.title}</span>
                            </span>
                            <span class="chat-session-item-time">
                              {formatRelativeTime(item.time?.updated ?? 0)}
                            </span>
                          </A>
                          <button
                             class="chat-session-delete"
                             title="删除会话"
                             aria-label={`删除会话 ${item.title}`}
                            onClick={(e) => {
                              e.preventDefault()
                              e.stopPropagation()
                              if (confirm("确认删除此会话？")) props.onDelete(item.id)
                            }}
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                              <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14" />
                            </svg>
                          </button>
                        </div>
                      )}
                    </For>
                  </Show>
                </div>
              )
            }}
          </For>
          </div>
          <button class="chat-logout-btn" onClick={handleLogout}>
            <span class="chat-account-avatar">A</span>
            <span>退出登录</span>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10 17l5-5-5-5M15 12H3"/><path d="M14 3h5a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-5"/></svg>
          </button>
        </div>
      </aside>
    </>
  )
}
