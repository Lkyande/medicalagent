import { For, Show, createEffect, createSignal, onCleanup } from "solid-js"
import {
  ApiError,
  backend,
  type EducationWechatRelease,
  type WechatAuthorization,
  type WorkspaceHtml,
  type WorkspaceHtmlArtifact,
} from "@/api/backend"
import { DialogBody, DialogFooter, DialogHeader, DialogTitle, DialogV2 } from "@/ui/v2/dialog-v2"
import { showToast } from "@/utils/toast"
import { waitForWechatAuthorization } from "./wechat-authorization"

const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"])
const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const WINDOW_MARGIN = 18
const WINDOW_MIN_WIDTH = 640
const WINDOW_MIN_HEIGHT = 480
const WINDOW_MAX_MARGIN = 8
const RESIZE_DIRECTIONS = ["n", "ne", "e", "se", "s", "sw", "w", "nw"] as const

type WindowBounds = { left: number; top: number; width: number; height: number }
type ResizeDirection = (typeof RESIZE_DIRECTIONS)[number]

export function EducationHtmlEditor(props: {
  sessionID: string
  path: string
  jobID?: string
  externalUrl?: string | null
  onDirtyChange?: (dirty: boolean) => void
  onSaved: (artifact: WorkspaceHtmlArtifact) => void
  onViewInWorkspace: (path: string) => void
}) {
  const [artifact, setArtifact] = createSignal<WorkspaceHtml>()
  const [frameHtml, setFrameHtml] = createSignal("")
  const [loading, setLoading] = createSignal(true)
  const [saving, setSaving] = createSignal(false)
  const [uploading, setUploading] = createSignal(false)
  const [syncingWechat, setSyncingWechat] = createSignal(false)
  const [publishingWechat, setPublishingWechat] = createSignal(false)
  const [wechatRelease, setWechatRelease] = createSignal<EducationWechatRelease | null>(null)
  const [dirty, setDirty] = createSignal(false)
  const [error, setError] = createSignal("")
  const [selectedImage, setSelectedImage] = createSignal<HTMLImageElement>()
  const [maximized, setMaximized] = createSignal(false)
  const [interacting, setInteracting] = createSignal(false)
  const [windowBounds, setWindowBounds] = createSignal<WindowBounds>()
  const [mobile, setMobile] = createSignal(window.matchMedia("(max-width: 760px)").matches)
  let frameRef: HTMLIFrameElement | undefined
  let dialogRef: HTMLElement | undefined
  let imageInputRef: HTMLInputElement | undefined
  let savedRange: Range | undefined
  let restoreBounds: WindowBounds | undefined
  let teardownFrame: (() => void) | undefined
  let stopPointerInteraction: (() => void) | undefined
  let initializationFrame: number | undefined
  let mobileLayout = mobile()
  let disposed = false
  let editVersion = 0

  const markDirty = () => {
    editVersion += 1
    setDirty(true)
  }

  const isMobileLayout = () => window.matchMedia("(max-width: 760px)").matches

  const clampBounds = (bounds: WindowBounds): WindowBounds => {
    const maxWidth = Math.max(1, window.innerWidth - WINDOW_MARGIN * 2)
    const maxHeight = Math.max(1, window.innerHeight - WINDOW_MARGIN * 2)
    const minWidth = Math.min(WINDOW_MIN_WIDTH, maxWidth)
    const minHeight = Math.min(WINDOW_MIN_HEIGHT, maxHeight)
    const width = Math.min(Math.max(bounds.width, minWidth), maxWidth)
    const height = Math.min(Math.max(bounds.height, minHeight), maxHeight)
    return {
      left: Math.min(Math.max(bounds.left, WINDOW_MARGIN), window.innerWidth - WINDOW_MARGIN - width),
      top: Math.min(Math.max(bounds.top, WINDOW_MARGIN), window.innerHeight - WINDOW_MARGIN - height),
      width,
      height,
    }
  }

  const applyBounds = (bounds: WindowBounds) => {
    if (!dialogRef) return
    setWindowBounds(bounds)
  }

  const currentBounds = (): WindowBounds | undefined => {
    const controlled = windowBounds()
    if (controlled) return controlled
    const rect = dialogRef?.getBoundingClientRect()
    if (!rect) return
    return { left: rect.left, top: rect.top, width: rect.width, height: rect.height }
  }

  const centerWindow = () => {
    if (!dialogRef || isMobileLayout()) return
    const rect = dialogRef.getBoundingClientRect()
    const width = Math.min(rect.width || 1240, window.innerWidth - WINDOW_MARGIN * 2)
    const height = Math.min(rect.height || 900, window.innerHeight - WINDOW_MARGIN * 2)
    applyBounds(clampBounds({
      left: (window.innerWidth - width) / 2,
      top: (window.innerHeight - height) / 2,
      width,
      height,
    }))
  }

  const maximizeWindow = () => {
    applyBounds({
      left: WINDOW_MAX_MARGIN,
      top: WINDOW_MAX_MARGIN,
      width: window.innerWidth - WINDOW_MAX_MARGIN * 2,
      height: window.innerHeight - WINDOW_MAX_MARGIN * 2,
    })
  }

  const bindDialog = (element: HTMLDivElement) => {
    dialogRef = element
    if (initializationFrame !== undefined) window.cancelAnimationFrame(initializationFrame)
    initializationFrame = window.requestAnimationFrame(() => {
      initializationFrame = undefined
      centerWindow()
    })
  }

  const trackPointer = (event: PointerEvent, move: (event: PointerEvent) => void) => {
    const target = event.currentTarget as HTMLElement
    const pointerID = event.pointerId
    stopPointerInteraction?.()
    target.setPointerCapture(pointerID)
    setInteracting(true)
    const finish = () => {
      target.removeEventListener("pointermove", move)
      target.removeEventListener("pointerup", finish)
      target.removeEventListener("pointercancel", finish)
      if (target.hasPointerCapture(pointerID)) target.releasePointerCapture(pointerID)
      setInteracting(false)
      stopPointerInteraction = undefined
    }
    target.addEventListener("pointermove", move)
    target.addEventListener("pointerup", finish)
    target.addEventListener("pointercancel", finish)
    stopPointerInteraction = finish
  }

  const startDrag = (event: PointerEvent) => {
    if (event.button !== 0 || maximized() || isMobileLayout()) return
    const target = event.target as Element
    if (target.closest("button, a, input, select")) return
    const start = currentBounds()
    if (!start) return
    event.preventDefault()
    const originX = event.clientX
    const originY = event.clientY
    trackPointer(event, (next) => {
      applyBounds(clampBounds({
        ...start,
        left: start.left + next.clientX - originX,
        top: start.top + next.clientY - originY,
      }))
    })
  }

  const startResize = (direction: ResizeDirection, event: PointerEvent) => {
    if (event.button !== 0 || maximized() || isMobileLayout()) return
    const start = currentBounds()
    if (!start) return
    event.preventDefault()
    event.stopPropagation()
    const originX = event.clientX
    const originY = event.clientY
    const startRight = start.left + start.width
    const startBottom = start.top + start.height
    trackPointer(event, (next) => {
      const dx = next.clientX - originX
      const dy = next.clientY - originY
      const minWidth = Math.min(WINDOW_MIN_WIDTH, window.innerWidth - WINDOW_MARGIN * 2)
      const minHeight = Math.min(WINDOW_MIN_HEIGHT, window.innerHeight - WINDOW_MARGIN * 2)
      let left = start.left
      let right = startRight
      let top = start.top
      let bottom = startBottom

      if (direction.includes("w")) left = Math.min(Math.max(start.left + dx, WINDOW_MARGIN), startRight - minWidth)
      if (direction.includes("e")) right = Math.max(Math.min(startRight + dx, window.innerWidth - WINDOW_MARGIN), start.left + minWidth)
      if (direction.includes("n")) top = Math.min(Math.max(start.top + dy, WINDOW_MARGIN), startBottom - minHeight)
      if (direction.includes("s")) bottom = Math.max(Math.min(startBottom + dy, window.innerHeight - WINDOW_MARGIN), start.top + minHeight)

      applyBounds({ left, top, width: right - left, height: bottom - top })
    })
  }

  const toggleMaximize = (event: MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()
    if (isMobileLayout()) return
    if (maximized()) {
      setMaximized(false)
      if (restoreBounds) applyBounds(clampBounds(restoreBounds))
      else centerWindow()
      return
    }
    restoreBounds = currentBounds()
    setMaximized(true)
    maximizeWindow()
  }

  const handleViewportResize = () => {
    const nextMobile = isMobileLayout()
    if (nextMobile) {
      stopPointerInteraction?.()
      setMaximized(false)
      setMobile(true)
      setWindowBounds(undefined)
      restoreBounds = undefined
    } else if (mobileLayout) {
      setMobile(false)
      centerWindow()
    } else if (maximized()) {
      maximizeWindow()
    } else {
      const bounds = currentBounds()
      if (bounds) applyBounds(clampBounds(bounds))
    }
    mobileLayout = nextMobile
  }

  const load = async (confirmDirty = false) => {
    if (confirmDirty && dirty() && !window.confirm("重新加载会丢失尚未保存的修改，是否继续？")) return
    setLoading(true)
    setError("")
    try {
      const htmlRequest = props.jobID
        ? backend.educationPreviewHtml(props.sessionID, props.jobID)
        : backend.workspaceHtml(props.path)
      const releaseRequest = props.jobID
        ? backend.educationWechatRelease(props.sessionID, props.jobID)
        : Promise.resolve(null)
      const [next, release] = await Promise.all([
        htmlRequest,
        releaseRequest,
      ])
      setArtifact(next)
      setWechatRelease(release)
      setFrameHtml(next.html)
      editVersion = 0
      setDirty(false)
      setSelectedImage(undefined)
    } catch (cause) {
      const routeNotLoaded = !props.jobID
        && cause instanceof ApiError
        && cause.status === 404
        && cause.message === "Not Found"
      setError(routeNotLoaded
        ? "后端尚未加载 HTML 编辑接口，请重启后端后重试。"
        : cause instanceof Error ? cause.message : "HTML 加载失败")
    } finally {
      setLoading(false)
    }
  }

  createEffect(() => {
    props.sessionID
    props.path
    props.jobID
    void load()
  })

  createEffect(() => props.onDirtyChange?.(dirty()))

  const setupFrame = () => {
    teardownFrame?.()
    const doc = frameRef?.contentDocument
    if (!doc?.body) return
    doc.designMode = "on"
    const editorStyle = doc.createElement("style")
    editorStyle.dataset.platformEditorStyle = "true"
    editorStyle.textContent = `
      html { min-height: 100%; background: #f4f5f8; }
      body { min-height: calc(100vh - 48px); margin: 24px auto; outline: none; box-shadow: 0 18px 55px rgba(31, 45, 40, .12); }
      img.platform-editor-selected { outline: 3px solid #3248af !important; outline-offset: 3px; }
    `
    doc.head?.append(editorStyle)
    const rememberSelection = () => {
      const selection = doc.getSelection()
      if (selection?.rangeCount) savedRange = selection.getRangeAt(0).cloneRange()
    }
    const selectElement = (event: MouseEvent) => {
      const target = event.target
      const ImageElement = doc.defaultView?.HTMLImageElement
      const image = ImageElement && target instanceof ImageElement ? target as HTMLImageElement : undefined
      const previous = selectedImage()
      if (previous && previous !== image) previous.classList.remove("platform-editor-selected")
      if (image) image.classList.add("platform-editor-selected")
      setSelectedImage(image)
    }
    doc.addEventListener("input", markDirty)
    doc.addEventListener("selectionchange", rememberSelection)
    doc.addEventListener("click", selectElement)
    teardownFrame = () => {
      doc.removeEventListener("input", markDirty)
      doc.removeEventListener("selectionchange", rememberSelection)
      doc.removeEventListener("click", selectElement)
    }
  }

  const restoreSelection = () => {
    const doc = frameRef?.contentDocument
    if (!doc || !savedRange) return
    const selection = doc.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(savedRange)
  }

  const command = (name: string, value?: string) => {
    const doc = frameRef?.contentDocument
    if (!doc) return
    restoreSelection()
    doc.execCommand(name, false, value)
    frameRef?.contentWindow?.focus()
    markDirty()
  }

  const editLink = () => {
    const url = window.prompt("输入链接地址（https://...）")?.trim()
    if (!url) return
    if (!url.startsWith("https://") && !url.startsWith("http://")) {
      setError("链接地址必须以 https:// 或 http:// 开头")
      return
    }
    command("createLink", url)
  }

  const serialize = () => {
    const doc = frameRef?.contentDocument
    if (!doc?.documentElement) throw new Error("编辑画布尚未准备完成")
    const clone = doc.documentElement.cloneNode(true) as HTMLElement
    clone.querySelectorAll("[data-platform-editor-style]").forEach((node) => node.remove())
    clone.querySelectorAll(".platform-editor-selected").forEach((node) => node.classList.remove("platform-editor-selected"))
    const doctype = doc.doctype ? `<!DOCTYPE ${doc.doctype.name}>\n` : ""
    return doctype + clone.outerHTML
  }

  const save = async () => {
    const current = artifact()
    if (!current || saving()) return
    if (!dirty()) return current
    setSaving(true)
    setError("")
    try {
      const html = serialize()
      const savedVersion = editVersion
      const saved = props.jobID
        ? await backend.saveEducationPreviewHtml(props.sessionID, props.jobID, html, current.revision)
        : await backend.saveWorkspaceHtml(props.path, html, current.revision)
      const next = { ...current, ...saved, html }
      setArtifact(next)
      if (editVersion === savedVersion) setDirty(false)
      props.onSaved(saved)
      showToast({
        class: "chat-preview-toast",
        variant: "success",
        title: props.jobID ? "排版已保存" : "HTML 已保存",
        description: "已保存到所有文件，可随时查看或继续编辑。",
        duration: 10_000,
        actions: [{ label: "在所有文件中查看", onClick: () => props.onViewInWorkspace(saved.path) }],
      })
      return next
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        setError("工作区文件已被其他页面或 Agent 修改。请重新加载后再编辑，当前修改不会自动覆盖。")
      } else {
        setError(cause instanceof Error ? cause.message : "HTML 保存失败")
      }
      return undefined
    } finally {
      setSaving(false)
    }
  }

  const openWechatAuthorizationPopup = (): Window | null =>
    window.open("about:blank", "wechat-official-authorization", "width=980,height=760,resizable=yes,scrollbars=yes")

  const authorizeWechat = async (): Promise<WechatAuthorization> => {
    const popup = openWechatAuthorizationPopup()
    const authorization = await backend.createWechatAuthorization()
    if (!authorization.authUrl) throw new Error("未获取到微信公众号授权地址")
    const authorizationID = authorization.authorizationId
    if (!authorizationID) throw new Error("未获取到微信公众号授权记录")
    if (!popup) throw new Error("浏览器阻止了授权窗口，请允许本站打开弹窗后重试")
    popup.location.href = authorization.authUrl
    try {
      return await waitForWechatAuthorization({
        check: () => backend.wechatAuthorizationAttempt(authorizationID),
        popupClosed: () => popup.closed,
        disposed: () => disposed,
      })
    } finally {
      if (!popup.closed) popup.close()
    }
  }

  const syncWechatDraft = async () => {
    const jobID = props.jobID
    if (!jobID || syncingWechat() || publishingWechat() || loading()) return
    setSyncingWechat(true)
    setError("")
    try {
      const current = dirty() ? await save() : artifact()
      if (!current) return
      if (dirty()) throw new Error("保存期间排版又发生了修改，请再次同步草稿")
      let authorization = await backend.wechatAuthorization()
      if (!authorization.authorized) authorization = await authorizeWechat()
      let release: EducationWechatRelease
      try {
        release = await backend.syncEducationWechatDraft(props.sessionID, jobID, current.revision)
      } catch (cause) {
        const authorizationExpired = cause instanceof ApiError
          && cause.status === 409
          && cause.message.toLowerCase().includes("authorization")
        if (!authorizationExpired) throw cause
        authorization = await authorizeWechat()
        release = await backend.syncEducationWechatDraft(props.sessionID, jobID, current.revision)
      }
      setWechatRelease(release)
      showToast({
        class: "chat-preview-toast",
        variant: "success",
        title: "已同步到公众号草稿箱",
        description: `公众号：${release.authorizerAppid ?? authorization.authorizerAppid ?? "已授权账号"}`,
        duration: 8000,
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "同步微信公众号草稿失败")
    } finally {
      setSyncingWechat(false)
    }
  }

  const publishWechatArticle = async () => {
    const jobID = props.jobID
    const release = wechatRelease()
    const current = artifact()
    if (!jobID || !release?.mediaId || !current || publishingWechat()) return
    if (dirty() || release.contentRevision !== current.revision) {
      setError("当前排版在同步草稿后发生过修改，请重新同步草稿再发布")
      return
    }
    if (!window.confirm("确认正式发布这篇微信公众号文章？提交后将进入微信发布流程。")) return
    setPublishingWechat(true)
    setError("")
    try {
      const published = await backend.publishEducationWechatDraft(props.sessionID, jobID)
      setWechatRelease(published)
      showToast({
        class: "chat-preview-toast",
        variant: "success",
        title: "文章已提交发布",
        description: published.publishId ? `发布任务：${published.publishId}` : "微信已接收发布请求。",
        duration: 10_000,
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "微信公众号文章发布失败")
    } finally {
      setPublishingWechat(false)
    }
  }

  const resolveWechatPublish = async (published: boolean) => {
    const jobID = props.jobID
    if (!jobID) return
    const message = published
      ? "请确认你已在微信公众平台看到该文章进入发布流程。确认后本页面会标记为已提交。"
      : "请确认你已在微信公众平台核实该文章没有进入发布流程。确认后才能重新同步或发布。"
    if (!window.confirm(message)) return
    setPublishingWechat(true)
    setError("")
    try {
      const resolved = await backend.resolveEducationWechatPublish(props.sessionID, jobID, published)
      setWechatRelease(resolved)
      showToast({
        class: "chat-preview-toast",
        variant: "success",
        title: published ? "已确认文章进入发布流程" : "已解除发布锁定",
        description: published ? "当前排版已标记为已提交发布。" : "现在可以重新同步草稿或发布。",
        duration: 8000,
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "确认发布结果失败")
    } finally {
      setPublishingWechat(false)
    }
  }

  const chooseImage = () => imageInputRef?.click()

  const uploadImage = async (event: Event) => {
    const input = event.currentTarget as HTMLInputElement
    const file = input.files?.[0]
    input.value = ""
    if (!file || uploading()) return
    if (!IMAGE_TYPES.has(file.type)) {
      setError("仅支持 PNG、JPEG、GIF 和 WebP 图片")
      return
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setError("图片不能超过 10 MiB")
      return
    }
    setUploading(true)
    setError("")
    try {
      const url = props.jobID
        ? (await backend.uploadEducationPreviewImage(props.sessionID, props.jobID, file)).url
        : await new Promise<string>((resolve, reject) => {
            const reader = new FileReader()
            reader.addEventListener("load", () => typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("图片读取失败")))
            reader.addEventListener("error", () => reject(reader.error ?? new Error("图片读取失败")))
            reader.readAsDataURL(file)
          })
      const image = selectedImage()
      if (image?.isConnected) {
        image.src = url
        if (image.hasAttribute("data-src")) image.setAttribute("data-src", url)
        image.removeAttribute("srcset")
        markDirty()
      } else {
        command("insertImage", url)
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "图片上传失败")
    } finally {
      setUploading(false)
    }
  }

  const keyboardSave = (event: KeyboardEvent) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault()
      void save()
    }
  }
  window.addEventListener("keydown", keyboardSave)
  window.addEventListener("resize", handleViewportResize)
  const beforeUnload = (event: BeforeUnloadEvent) => {
    if (!dirty()) return
    event.preventDefault()
    event.returnValue = ""
  }
  window.addEventListener("beforeunload", beforeUnload)
  onCleanup(() => {
    disposed = true
    teardownFrame?.()
    stopPointerInteraction?.()
    if (initializationFrame !== undefined) window.cancelAnimationFrame(initializationFrame)
    window.removeEventListener("keydown", keyboardSave)
    window.removeEventListener("resize", handleViewportResize)
    window.removeEventListener("beforeunload", beforeUnload)
  })

  const toolbarMouseDown = (event: MouseEvent) => event.preventDefault()

  return (
    <DialogV2 size="x-large" class="chat-education-editor-layer">
      <div
        ref={(element) => bindDialog(element)}
        class="chat-education-editor-dialog"
        classList={{ maximized: maximized(), "is-interacting": interacting() }}
        style={mobile() || !windowBounds() ? undefined : {
          position: "fixed",
          inset: "auto",
          transform: "none",
          left: `${windowBounds()!.left}px`,
          top: `${windowBounds()!.top}px`,
          width: `${windowBounds()!.width}px`,
          height: `${windowBounds()!.height}px`,
        }}
      >
        <DialogHeader>
          <div
            class="chat-education-editor-titlebar"
            onPointerDown={startDrag}
            onDblClick={toggleMaximize}
          >
            <DialogTitle>{props.jobID ? "编辑公众号排版" : "编辑 HTML"}</DialogTitle>
            <div class="chat-education-editor-meta">
              <code>{artifact()?.path ?? props.path}</code>
              <span classList={{ dirty: dirty() }}>{dirty() ? "有未保存修改" : "已保存"}</span>
            </div>
          </div>
          <button
            type="button"
            class="chat-education-editor-maximize"
            aria-label={maximized() ? "还原窗口" : "最大化窗口"}
            title={maximized() ? "还原窗口" : "最大化窗口"}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={toggleMaximize}
          >
            <Show
              when={maximized()}
              fallback={<svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><rect x="2.5" y="2.5" width="9" height="9" rx="1" stroke="currentColor" /></svg>}
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M4.5 4.5V3.3c0-.44.36-.8.8-.8h5.4c.44 0 .8.36.8.8v5.4c0 .44-.36.8-.8.8H9.5M3.3 4.5h5.4c.44 0 .8.36.8.8v5.4c0 .44-.36.8-.8.8H3.3a.8.8 0 0 1-.8-.8V5.3c0-.44.36-.8.8-.8Z" stroke="currentColor" /></svg>
            </Show>
          </button>
        </DialogHeader>
        <DialogBody class="chat-education-editor-body">
          <div class="chat-education-editor-toolbar" aria-label="排版工具栏">
            <select aria-label="段落格式" onChange={(event) => command("formatBlock", event.currentTarget.value)} disabled={loading()}>
              <option value="p">正文</option>
              <option value="h1">标题 1</option>
              <option value="h2">标题 2</option>
              <option value="h3">标题 3</option>
              <option value="blockquote">引用</option>
            </select>
            <button type="button" title="加粗" onMouseDown={toolbarMouseDown} onClick={() => command("bold")}><strong>B</strong></button>
            <button type="button" title="斜体" onMouseDown={toolbarMouseDown} onClick={() => command("italic")}><em>I</em></button>
            <button type="button" title="下划线" onMouseDown={toolbarMouseDown} onClick={() => command("underline")}><u>U</u></button>
            <button type="button" title="删除线" onMouseDown={toolbarMouseDown} onClick={() => command("strikeThrough")}><s>S</s></button>
            <span class="chat-education-editor-divider" />
            <button type="button" title="无序列表" onMouseDown={toolbarMouseDown} onClick={() => command("insertUnorderedList")}>• 列表</button>
            <button type="button" title="有序列表" onMouseDown={toolbarMouseDown} onClick={() => command("insertOrderedList")}>1. 列表</button>
            <button type="button" title="左对齐" onMouseDown={toolbarMouseDown} onClick={() => command("justifyLeft")}>左</button>
            <button type="button" title="居中" onMouseDown={toolbarMouseDown} onClick={() => command("justifyCenter")}>中</button>
            <button type="button" title="右对齐" onMouseDown={toolbarMouseDown} onClick={() => command("justifyRight")}>右</button>
            <span class="chat-education-editor-divider" />
            <button type="button" onMouseDown={toolbarMouseDown} onClick={editLink}>添加链接</button>
            <button type="button" onMouseDown={toolbarMouseDown} onClick={() => command("unlink")}>移除链接</button>
            <button type="button" onMouseDown={toolbarMouseDown} onClick={() => command("removeFormat")}>清除格式</button>
            <button type="button" onMouseDown={toolbarMouseDown} onClick={() => command("undo")}>撤销</button>
            <button type="button" onMouseDown={toolbarMouseDown} onClick={() => command("redo")}>重做</button>
            <button type="button" classList={{ active: !!selectedImage() }} onClick={chooseImage} disabled={uploading()}>{uploading() ? "上传中" : selectedImage() ? "替换图片" : "插入图片"}</button>
            <input ref={imageInputRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden onChange={(event) => void uploadImage(event)} />
          </div>
          <Show when={error()}><div class="chat-error chat-education-editor-error" role="alert">{error()}</div></Show>
          <div class="chat-education-editor-canvas" aria-busy={loading()}>
            <Show when={!loading()} fallback={<div class="chat-loading">正在加载工作区 HTML...</div>}>
              <iframe
                ref={frameRef}
                title={props.jobID ? "公众号排版可视化编辑画布" : "HTML 可视化编辑画布"}
                sandbox="allow-same-origin"
                srcdoc={frameHtml()}
                onLoad={setupFrame}
              />
            </Show>
          </div>
        </DialogBody>
        <DialogFooter>
          <div class="chat-education-editor-footer-copy">
            <Show when={props.jobID} fallback={<>可视化编辑内容将保存回当前 HTML 文件。</>}>
              <Show when={wechatRelease()?.mediaId} fallback={<>同步前会先保存工作区 HTML。</>}>
              已同步公众号草稿
              <Show when={wechatRelease()?.authorizerAppid}> · {wechatRelease()!.authorizerAppid}</Show>
              <Show when={wechatRelease()?.status === "submitted"}> · 已提交发布</Show>
              <Show when={wechatRelease()?.status === "unknown"}> · 发布结果待人工确认</Show>
              </Show>
            </Show>
          </div>
          <div class="chat-education-editor-footer-actions">
            <Show when={props.externalUrl}><a href={props.externalUrl!} target="_blank" rel="noreferrer">打开原始预览</a></Show>
            <button type="button" onClick={() => void load(true)} disabled={loading() || saving()}>重新加载</button>
            <button type="button" class="primary" onClick={() => void save()} disabled={!dirty() || saving() || loading()}>{saving() ? "保存中..." : "保存 HTML"}</button>
            <Show when={props.jobID}>
            <button
              type="button"
              class="wechat-draft"
              onClick={() => void syncWechatDraft()}
              disabled={
                loading()
                || saving()
                || syncingWechat()
                || publishingWechat()
                || wechatRelease()?.status === "submitted"
                || wechatRelease()?.status === "unknown"
              }
            >
                {syncingWechat() ? "同步中..." : wechatRelease()?.mediaId ? "重新同步草稿" : "同步公众号草稿"}
              </button>
            <Show
              when={wechatRelease()?.status === "unknown"}
              fallback={
                <button
                  type="button"
                  class="wechat-publish"
                  onClick={() => void publishWechatArticle()}
                  disabled={
                    loading()
                    || saving()
                    || syncingWechat()
                    || publishingWechat()
                    || dirty()
                    || !wechatRelease()?.mediaId
                    || wechatRelease()?.contentRevision !== artifact()?.revision
                    || wechatRelease()?.status === "submitted"
                  }
                >
                  {publishingWechat() ? "发布中..." : wechatRelease()?.status === "submitted" ? "已提交发布" : "发布文章"}
                </button>
              }
            >
              <button type="button" class="wechat-draft" disabled={publishingWechat()} onClick={() => void resolveWechatPublish(false)}>
                确认未发布
              </button>
              <button type="button" class="wechat-publish" disabled={publishingWechat()} onClick={() => void resolveWechatPublish(true)}>
                确认已发布
              </button>
            </Show>
            </Show>
          </div>
        </DialogFooter>
        <For each={RESIZE_DIRECTIONS}>
          {(direction) => (
            <span
              class="chat-education-editor-resize-handle"
              data-direction={direction}
              aria-hidden="true"
              onPointerDown={(event) => startResize(direction, event)}
            />
          )}
        </For>
      </div>
    </DialogV2>
  )
}
