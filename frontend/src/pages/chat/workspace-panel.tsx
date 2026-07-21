import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js"
import { backend } from "@/api/backend"
import { DialogBody, DialogHeader, DialogTitle, DialogV2 } from "@/ui/v2/dialog-v2"
import { FileIcon } from "@/ui/file-icon"
import { useDialog } from "@/ui/context/dialog"
import { EducationHtmlEditor } from "./education-html-editor"

export interface WorkspaceNode {
  name: string
  path: string
  type: "file" | "directory"
  ignored: boolean
}

const sortNodes = (nodes: WorkspaceNode[]) =>
  [...nodes].sort((a, b) => {
    if (a.type !== b.type) return a.type === "directory" ? -1 : 1
    return a.name.localeCompare(b.name)
  })

type FileCategory = "all" | "media" | "document" | "code" | "other"

const categories: Array<{ value: FileCategory; label: string }> = [
  { value: "all", label: "全部" },
  { value: "media", label: "图片和视频" },
  { value: "document", label: "文档" },
  { value: "code", label: "代码" },
  { value: "other", label: "其他" },
]

const mediaExtensions = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "avif", "mp4", "webm", "mov", "mkv"])
const documentExtensions = new Set(["md", "mdx", "txt", "pdf", "doc", "docx", "rtf", "csv", "xls", "xlsx", "ppt", "pptx"])
const codeExtensions = new Set([
  "js", "jsx", "ts", "tsx", "css", "scss", "html", "json", "yaml", "yml", "toml", "py", "go", "rs", "java", "kt", "c", "h", "cpp", "hpp", "cs", "php", "rb", "sh", "sql", "vue", "svelte",
])
const WORKSPACE_POLL_INTERVAL_MS = 30_000
const WORKSPACE_EVENT_DEBOUNCE_MS = 400
const EDUCATION_PREVIEW_PATH = /^education-previews\/([A-Za-z0-9_-]{1,80})\.html$/
const HTML_FILE_PATH = /\.html?$/i

function categoryOf(node: WorkspaceNode): Exclude<FileCategory, "all"> {
  const extension = node.name.split(".").at(-1)?.toLowerCase() ?? ""
  if (mediaExtensions.has(extension)) return "media"
  if (documentExtensions.has(extension)) return "document"
  if (codeExtensions.has(extension)) return "code"
  return "other"
}

export function WorkspacePanel(props: {
  refreshVersion: number
  sessionID?: string
  educationJobIDs?: string[]
  initialPath?: string
  onWorkspaceChanged?: () => void
}) {
  const dialog = useDialog()
  const [directory, setDirectory] = createSignal("")
  const [children, setChildren] = createSignal<Record<string, WorkspaceNode[]>>({})
  const [expanded, setExpanded] = createSignal<Record<string, boolean>>({})
  const [loading, setLoading] = createSignal(true)
  const [error, setError] = createSignal("")
  const [selectedPath, setSelectedPath] = createSignal("")
  const [content, setContent] = createSignal("")
  const [contentType, setContentType] = createSignal<"text" | "binary">("text")
  const [contentLoading, setContentLoading] = createSignal(false)
  const [downloading, setDownloading] = createSignal("")
  const [deleting, setDeleting] = createSignal("")
  const [actionError, setActionError] = createSignal("")
  const [category, setCategory] = createSignal<FileCategory>("all")
  const [highlightedPath, setHighlightedPath] = createSignal(props.initialPath ?? "")
  const directoryRequests = new Map<string, number>()
  let contentRequest = 0
  let pollTimer: number | undefined
  let eventRefreshTimer: number | undefined
  let refreshPromise: Promise<boolean> | undefined
  let refreshAgain = false
  let initialized = false
  let pendingEventRefresh = false
  let disposed = false

  const loadDirectory = async (path: string, silent = false): Promise<boolean> => {
    const request = (directoryRequests.get(path) ?? 0) + 1
    directoryRequests.set(path, request)
    if (!silent) setLoading(true)
    try {
      const nodes = sortNodes(await backend.workspaceFiles(path))
      if (directoryRequests.get(path) !== request || disposed) return false
      setChildren((current) => ({ ...current, [path]: nodes }))
      setError("")
      return true
    } catch (cause: any) {
      if (directoryRequests.get(path) !== request || disposed) return false
      setError(cause?.message ?? "工作目录加载失败")
      return false
    } finally {
      if (!disposed && path === "" && directoryRequests.get(path) === request) setLoading(false)
    }
  }

  const runRefresh = async (silent: boolean) => {
    const rootLoaded = await loadDirectory("", silent)
    if (!rootLoaded || disposed) return false
    const paths = Object.keys(expanded()).filter((path) => expanded()[path])
    await Promise.all(paths.map((path) => loadDirectory(path, true)))
    return true
  }

  const refresh = (silent = false): Promise<boolean> => {
    if (refreshPromise) {
      refreshAgain = true
      return refreshPromise
    }

    const current = (async () => {
      let success = false
      let nextSilent = silent
      do {
        refreshAgain = false
        success = await runRefresh(nextSilent)
        nextSilent = true
      } while (refreshAgain && !disposed)
      return success
    })()
    refreshPromise = current
    void current.finally(() => {
      if (refreshPromise === current) refreshPromise = undefined
    })
    return current
  }

  const initialize = async () => {
    try {
      const [workspace, loaded] = await Promise.all([backend.workspace(), refresh()])
      if (disposed) return
      setDirectory(workspace.directory)
      if (!loaded || disposed) return
      initialized = true
      const initialPath = props.initialPath
      if (initialPath) {
        const parts = initialPath.split("/").filter(Boolean)
        let parent = ""
        for (const part of parts.slice(0, -1)) {
          parent = parent ? `${parent}/${part}` : part
          setExpanded((current) => ({ ...current, [parent]: true }))
          await loadDirectory(parent, true)
        }
      }
      if (pendingEventRefresh) {
        pendingEventRefresh = false
        void refresh(true)
      }
      pollTimer = window.setInterval(() => void refresh(true), WORKSPACE_POLL_INTERVAL_MS)
    } catch (cause: any) {
      if (disposed) return
      setError(cause?.message ?? "工作目录初始化失败")
      setLoading(false)
    }
  }

  onMount(() => {
    void initialize()
    onCleanup(() => {
      disposed = true
      if (pollTimer !== undefined) window.clearInterval(pollTimer)
      if (eventRefreshTimer !== undefined) window.clearTimeout(eventRefreshTimer)
    })
  })

  let lastRefresh = props.refreshVersion
  createEffect(() => {
    const version = props.refreshVersion
    if (version === lastRefresh) return
    lastRefresh = version
    if (!initialized) {
      pendingEventRefresh = true
      return
    }
    if (eventRefreshTimer !== undefined) window.clearTimeout(eventRefreshTimer)
    eventRefreshTimer = window.setTimeout(() => {
      eventRefreshTimer = undefined
      void refresh(true)
    }, WORKSPACE_EVENT_DEBOUNCE_MS)
  })

  const toggleDirectory = async (path: string) => {
    const next = !expanded()[path]
    setExpanded((current) => ({ ...current, [path]: next }))
    if (next && !children()[path]) await loadDirectory(path, true)
  }

  const revealFile = async (path: string) => {
    setCategory("all")
    setSelectedPath("")
    setHighlightedPath("")
    const parts = path.split("/").filter(Boolean)
    let parent = ""
    for (const part of parts.slice(0, -1)) {
      parent = parent ? `${parent}/${part}` : part
      setExpanded((current) => ({ ...current, [parent]: true }))
      await loadDirectory(parent, true)
    }
    await refresh(true)
    setHighlightedPath(path)
  }

  const openFile = async (path: string) => {
    setHighlightedPath("")
    const preview = EDUCATION_PREVIEW_PATH.exec(path)
    if (HTML_FILE_PATH.test(path)) {
      const previewJobID = preview?.[1]
      const jobID = props.sessionID && previewJobID && props.educationJobIDs?.includes(previewJobID) ? previewJobID : undefined
      let editorDirty = false
      void dialog.push(() => (
        <EducationHtmlEditor
          sessionID={props.sessionID ?? ""}
          path={path}
          jobID={jobID}
          onDirtyChange={(dirty) => { editorDirty = dirty }}
          onSaved={(artifact) => {
            props.onWorkspaceChanged?.()
            void revealFile(artifact.path)
          }}
          onViewInWorkspace={(savedPath) => {
            void revealFile(savedPath)
            dialog.close()
          }}
        />
      ), () => !editorDirty || window.confirm("排版还有未保存的修改，确定关闭编辑器吗？"))
      return
    }
    const request = ++contentRequest
    setSelectedPath(path)
    setContentLoading(true)
    try {
      const result = await backend.workspaceContent(path)
      if (request !== contentRequest || selectedPath() !== path) return
      setContentType(result.type)
      setContent(result.content)
    } catch (cause: any) {
      if (request !== contentRequest || selectedPath() !== path) return
      setContentType("text")
      setContent(cause?.message ?? "文件读取失败")
    } finally {
      if (request === contentRequest) setContentLoading(false)
    }
  }

  const downloadFile = async (path: string) => {
    setDownloading(path)
    setActionError("")
    try {
      await backend.downloadWorkspaceFile(path)
    } catch (cause: any) {
      setActionError(cause?.message ?? "文件下载失败")
    } finally {
      setDownloading("")
    }
  }

  const downloadArchive = async () => {
    setDownloading("__archive__")
    setActionError("")
    try {
      await backend.downloadWorkspaceArchive()
    } catch (cause: any) {
      setActionError(cause?.message ?? "工作目录打包失败")
    } finally {
      setDownloading("")
    }
  }

  const deleteEntry = async (node: WorkspaceNode) => {
    const kind = node.type === "directory" ? "空目录" : "文件"
    if (!confirm(`确认永久删除${kind}“${node.path}”？此操作无法恢复。`)) return
    setDeleting(node.path)
    setActionError("")
    try {
      await backend.deleteWorkspaceEntry(node.path)
      if (selectedPath() === node.path) {
        contentRequest += 1
        setSelectedPath("")
      }
      if (node.type === "directory") {
        setExpanded((current) => {
          const next = { ...current }
          delete next[node.path]
          return next
        })
        setChildren((current) => {
          const next = { ...current }
          delete next[node.path]
          return next
        })
      }
      await refresh(true)
    } catch (cause: any) {
      setActionError(cause?.message ?? "删除失败")
    } finally {
      setDeleting("")
    }
  }

  const directoryName = createMemo(() => directory().split(/[\\/]/).filter(Boolean).at(-1) ?? "workspace")

  return (
    <DialogV2 size="large" class="chat-files-dialog">
      <DialogHeader closeLabel="关闭文件对话框">
        <div class="chat-files-title-group">
          <DialogTitle>此任务中的所有文件</DialogTitle>
          <span title={directory()}>{directoryName()}</span>
        </div>
      </DialogHeader>
      <DialogBody class="chat-files-dialog-body">
        <div class="chat-files-toolbar">
          <div class="chat-files-categories" role="tablist" aria-label="文件类型">
            <For each={categories}>
              {(item) => (
                <button
                  type="button"
                  role="tab"
                  aria-selected={category() === item.value}
                  classList={{ active: category() === item.value }}
                  onClick={() => setCategory(item.value)}
                >
                  {item.label}
                </button>
              )}
            </For>
          </div>
          <div class="chat-workspace-header-actions">
            <button
              type="button"
              onClick={() => void downloadArchive()}
              disabled={!!downloading()}
              aria-label="下载全部文件 ZIP"
              title="下载全部文件 ZIP"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M5 19h14"/></svg>
            </button>
            <button type="button" onClick={() => void refresh()} aria-label="刷新文件" title="刷新文件">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 9a7 7 0 0 1 11.7-2L20 9M4 15l2.2 2a7 7 0 0 0 11.7-2"/></svg>
            </button>
          </div>
        </div>
        <div class="chat-files-content" aria-busy={loading() || contentLoading()}>
        <Show when={selectedPath()}>
          <div class="chat-workspace-preview">
            <div class="chat-workspace-preview-header">
              <button type="button" onClick={() => { contentRequest += 1; setSelectedPath("") }} aria-label="返回文件列表">←</button>
              <span title={selectedPath()}>{selectedPath()}</span>
              <div class="chat-workspace-preview-actions">
                <button
                  type="button"
                  onClick={() => void downloadFile(selectedPath())}
                  disabled={!!downloading()}
                  aria-label="下载文件"
                  title="下载文件"
                >
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M5 19h14"/></svg>
                </button>
                <button
                  type="button"
                  class="chat-workspace-danger"
                  onClick={() => void deleteEntry({ name: selectedPath().split("/").at(-1) ?? selectedPath(), path: selectedPath(), type: "file", ignored: false })}
                  disabled={!!deleting()}
                  aria-label="永久删除文件"
                  title="永久删除文件"
                >
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5"/></svg>
                </button>
              </div>
            </div>
            <Show when={!contentLoading()} fallback={<div class="chat-workspace-state">读取中...</div>}>
              <Show when={contentType() === "text"} fallback={<div class="chat-workspace-state">二进制文件无法预览</div>}>
                <pre>{content()}</pre>
              </Show>
            </Show>
          </div>
        </Show>

        <Show when={!selectedPath()}>
          <div class="chat-workspace-tree">
            <Show when={!loading()} fallback={<div class="chat-workspace-state">加载中...</div>}>
              <Show when={!error()} fallback={<div class="chat-workspace-state chat-workspace-error">{error()}</div>}>
                <WorkspaceTree
                  nodes={children()[""] ?? []}
                  children={children()}
                  expanded={expanded()}
                  category={category()}
                  depth={0}
                  onToggle={toggleDirectory}
                  onOpen={openFile}
                  onDownload={downloadFile}
                  onDelete={deleteEntry}
                  downloading={downloading()}
                  deleting={deleting()}
                  highlightedPath={highlightedPath()}
                />
                <Show when={(children()[""] ?? []).length === 0}>
                  <div class="chat-workspace-state">目录为空，Agent 创建的文件会显示在这里</div>
                </Show>
              </Show>
            </Show>
          </div>
        </Show>
        <Show when={actionError()}>
          <div class="chat-workspace-action-error" role="alert">{actionError()}</div>
        </Show>
        </div>
      </DialogBody>
    </DialogV2>
  )
}

function WorkspaceTree(props: {
  nodes: WorkspaceNode[]
  children: Record<string, WorkspaceNode[]>
  expanded: Record<string, boolean>
  category: FileCategory
  depth: number
  onToggle: (path: string) => void
  onOpen: (path: string) => void
  onDownload: (path: string) => void
  onDelete: (node: WorkspaceNode) => void
  downloading: string
  deleting: string
  highlightedPath: string
}) {
  const visible = () => props.nodes.filter((node) => node.type === "directory" || props.category === "all" || categoryOf(node) === props.category)
  return (
    <For each={visible()}>
      {(node) => (
        <>
          <div
            class="chat-workspace-node-row"
            classList={{ "chat-workspace-node-highlighted": node.path === props.highlightedPath }}
            ref={(element) => {
              if (node.path === props.highlightedPath) {
                queueMicrotask(() => element.scrollIntoView({ block: "nearest" }))
              }
            }}
            style={{ "padding-left": `${8 + props.depth * 16}px` }}
          >
            <button
              type="button"
              class="chat-workspace-node"
              classList={{ "chat-workspace-node-directory": node.type === "directory" }}
              aria-current={node.path === props.highlightedPath ? "true" : undefined}
              onClick={() => node.type === "directory" ? props.onToggle(node.path) : props.onOpen(node.path)}
              title={node.path}
            >
              <span class="chat-workspace-chevron">
                {node.type === "directory" ? (props.expanded[node.path] ? "▾" : "▸") : ""}
              </span>
              <FileIcon
                node={{ path: node.path, type: node.type }}
                expanded={node.type === "directory" && props.expanded[node.path]}
                class="chat-workspace-file-icon"
              />
              <span class="chat-workspace-node-name">{node.name}</span>
            </button>
            <div class="chat-workspace-node-actions">
              <Show when={node.type === "file"}>
                <button
                  type="button"
                  onClick={() => props.onDownload(node.path)}
                  disabled={!!props.downloading}
                  aria-label={`下载 ${node.name}`}
                  title="下载"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M5 19h14"/></svg>
                </button>
              </Show>
              <button
                type="button"
                class="chat-workspace-danger"
                onClick={() => props.onDelete(node)}
                disabled={!!props.deleting}
                aria-label={`永久删除 ${node.name}`}
                title={node.type === "directory" ? "删除空目录" : "永久删除"}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5"/></svg>
              </button>
            </div>
          </div>
          <Show when={node.type === "directory" && props.expanded[node.path]}>
            <WorkspaceTree
              nodes={props.children[node.path] ?? []}
              children={props.children}
              expanded={props.expanded}
              category={props.category}
              depth={props.depth + 1}
              onToggle={props.onToggle}
              onOpen={props.onOpen}
              onDownload={props.onDownload}
              onDelete={props.onDelete}
              downloading={props.downloading}
              deleting={props.deleting}
              highlightedPath={props.highlightedPath}
            />
          </Show>
        </>
      )}
    </For>
  )
}
