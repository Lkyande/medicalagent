import { createMemo, For, Show, createSignal, onCleanup, onMount } from "solid-js"
import { useNavigate, A } from "@solidjs/router"
import { setToken } from "./chat-client"
import { groupByTime, formatRelativeTime } from "./group-by-time"

export interface SidebarSession {
  id: string
  title: string
  time: number
}

export function ChatSidebar(props: {
  sessions: SidebarSession[]
  loading: boolean
  currentSessionId?: string
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
    groupByTime(props.sessions.map((s) => ({ id: s.id, title: s.title, time: { updated: s.time } }))),
  )

  const newChat = () => {
    props.onClose()
    navigate("/chat")
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
          <button class="chat-rail-action" onClick={props.onExpand} aria-label="查看历史会话" title="历史会话">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>
          </button>
          <button class="chat-rail-action chat-rail-logout" onClick={handleLogout} aria-label="退出登录" title="退出登录">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10 17l5-5-5-5M15 12H3"/><path d="M14 3h5a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-5"/></svg>
          </button>
        </div>
        <div class="chat-sidebar-expanded">
          <div class="chat-sidebar-header">
            <div class="chat-sidebar-brand"><span>A</span><strong>Agnes Workspace</strong></div>
            <button class="chat-sidebar-collapse" onClick={props.onCollapse} aria-label="收起历史会话" title="收起历史会话">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M15 18l-6-6 6-6" /></svg>
            </button>
          </div>
          <div class="chat-sidebar-new-wrap">
            <button class="chat-new-btn" onClick={newChat}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14" /></svg>
              新建对话
            </button>
          </div>
          <div class="chat-session-list">
          <Show when={props.loading && props.sessions.length === 0}>
            <div class="chat-loading">加载中...</div>
          </Show>
          <Show when={!props.loading && props.sessions.length === 0}>
            <div class="chat-session-empty">暂无会话</div>
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
                            href={`/chat/${item.id}`}
                            class="chat-session-item"
                            classList={{ active: item.id === props.currentSessionId }}
                            onClick={props.onClose}
                          >
                            <span class="chat-session-item-title">{item.title}</span>
                            <span class="chat-session-item-time">
                              {formatRelativeTime(item.time?.updated ?? item.time?.created ?? 0)}
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
