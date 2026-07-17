export function ChatEmptyState(props: { clinical?: boolean; education?: boolean }) {
  return (
    <div class="chat-empty" classList={{ "chat-empty-clinical": props.clinical }}>
      <div class="chat-empty-inner">
        <h2>{props.clinical ? "今天需要什么诊疗帮助？" : props.education ? "今天想制作什么科普内容？" : "开始新对话"}</h2>
      </div>
    </div>
  )
}
