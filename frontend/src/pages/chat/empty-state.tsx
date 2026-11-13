export function ChatEmptyState(props: { clinical?: boolean; diseaseManagement?: boolean; education?: boolean; doorWindow?: boolean }) {
  return (
    <div class="chat-empty" classList={{ "chat-empty-clinical": props.clinical || props.diseaseManagement }}>
      <div class="chat-empty-inner">
        <h2>{props.clinical ? "今天需要什么诊疗帮助？" : props.diseaseManagement ? "今天需要制定或调整什么管理计划？" : props.education ? "今天想制作什么科普内容？" : props.doorWindow ? "上传 PDF 开始拆分门窗框图" : "开始新对话"}</h2>
      </div>
    </div>
  )
}
