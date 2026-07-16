import { Show } from "solid-js"

export function ChatHeader(props: {
  title: string
  generating?: boolean
  sidebarExpanded: boolean
  onToggleSidebar: () => void
  onOpenWorkspace: () => void
}) {
  return (
    <div class="chat-header">
      <div class="chat-header-left">
        <button
          class="chat-sidebar-toggle"
          onClick={props.onToggleSidebar}
          aria-label={props.sidebarExpanded ? "收起历史会话" : "展开历史会话"}
          aria-expanded={props.sidebarExpanded}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 12h18M3 6h18M3 18h18" />
          </svg>
        </button>
        <div class="chat-header-title">{props.title}</div>
        <Show when={props.generating}>
          <span class="chat-header-status">生成中...</span>
        </Show>
      </div>
      <button
        class="chat-workspace-toggle"
        onClick={props.onOpenWorkspace}
        aria-label="打开此任务中的所有文件"
        aria-haspopup="dialog"
        title="此任务中的所有文件"
      >
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
          <path d="M3 6.5A2.5 2.5 0 0 1 5.5 4H10l2 2h6.5A2.5 2.5 0 0 1 21 8.5v9A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5z" />
        </svg>
        <span>所有文件</span>
      </button>
    </div>
  )
}
