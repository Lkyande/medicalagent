import { createEffect, onCleanup } from "solid-js"
import { ApiError, backend } from "@/api/backend"
import { showToast } from "@/utils/toast"

function saveFailureDescription(cause: unknown): string {
  if (!(cause instanceof ApiError)) {
    return cause instanceof Error ? cause.message : "HTML 保存到工作区失败，请稍后重试。"
  }
  if (cause.status === 404 && cause.message === "Not Found") {
    return "平台后端尚未加载排版保存接口，请重启后端后重试。"
  }
  if (cause.status === 404 && cause.message === "Preview job not found") {
    return "当前会话无法确认该排版任务，请重新生成后再试。"
  }
  if (cause.status === 404 && cause.message === "Session not found") {
    return "当前会话已失效，请刷新页面后重试。"
  }
  if (cause.status === 502 && cause.message === "Completed preview did not include HTML") {
    return "该历史任务未保留排版 HTML，请重新生成一次公众号排版。"
  }
  if (cause.status === 502) return "暂时无法读取排版内容，请稍后重试。"
  return cause.message || "HTML 保存到工作区失败，请稍后重试。"
}

export function EducationPreviewCoordinator(props: {
  sessionID: string
  jobID: string
  onWorkspaceChanged: () => void
  onOpenWorkspace: (path: string) => void
}) {
  createEffect(() => {
    const sessionID = props.sessionID
    const jobID = props.jobID
    let active = true
    let loading = false
    let materializing = false
    let saved = false
    let failedNotified = false
    let timer: number | undefined

    const stopPolling = () => {
      if (timer === undefined) return
      window.clearInterval(timer)
      timer = undefined
    }

    const materialize = async () => {
      if (!active || materializing || saved) return
      materializing = true
      try {
        const artifact = await backend.materializeEducationPreview(sessionID, jobID)
        if (!active) return
        saved = true
        props.onWorkspaceChanged()
        showToast({
          class: "chat-preview-toast",
          variant: "success",
          title: "公众号排版已保存",
          description: "可在所有文件中查看或继续编辑。",
          duration: 10_000,
          actions: [{ label: "在所有文件中查看", onClick: () => props.onOpenWorkspace(artifact.path) }],
        })
      } catch (cause) {
        if (!active) return
        showToast({
          class: "chat-preview-toast",
          variant: "error",
          title: "公众号排版保存失败",
          description: saveFailureDescription(cause),
          persistent: true,
          actions: [{ label: "重试保存", onClick: () => void materialize() }],
        })
      } finally {
        materializing = false
      }
    }

    const refresh = async (): Promise<boolean> => {
      if (!active || loading || saved) return false
      loading = true
      try {
        const progress = await backend.educationPreviewProgress(sessionID, jobID)
        if (!active) return false
        if (progress.status === "completed") {
          await materialize()
          return false
        }
        if (progress.status === "failed") {
          if (!failedNotified) {
            failedNotified = true
            showToast({
              class: "chat-preview-toast",
              variant: "error",
              title: "公众号排版生成失败",
              description: `任务编号：${jobID}`,
            })
          }
          return false
        }
        return true
      } catch {
        return true
      } finally {
        loading = false
      }
    }

    const poll = async () => {
      if (!(await refresh())) stopPolling()
    }

    const start = async () => {
      try {
        await backend.educationPreviewHtml(sessionID, jobID)
        if (active) saved = true
        return
      } catch {}
      if (await refresh() && active) timer = window.setInterval(() => void poll(), 4_000)
    }

    void start()
    onCleanup(() => {
      active = false
      stopPolling()
    })
  })

  return null
}
