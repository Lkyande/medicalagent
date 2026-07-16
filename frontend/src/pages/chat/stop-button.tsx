export function ChatStopButton(props: { onClick: () => void; stopping?: boolean }) {
  return (
    <button
      type="button"
      class="chat-stop-btn"
      onClick={props.onClick}
      disabled={props.stopping}
      title={props.stopping ? "正在停止" : "停止生成"}
      aria-label={props.stopping ? "正在停止" : "停止生成"}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <rect x="6" y="6" width="12" height="12" rx="2" />
      </svg>
    </button>
  )
}
