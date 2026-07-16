import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js"
import { backend } from "@/api/backend"

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

export function WorkspacePanel(props: {
  collapsed: boolean
  open: boolean
  refreshVersion: number
  visible: boolean
  onCollapse: () => void
  onClose: () => void
}) {
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
  const directoryRequests = new Map<string, number>()
  let contentRequest = 0

  const loadDirectory = async (path: string, silent = false) => {
    const request = (directoryRequests.get(path) ?? 0) + 1
    directoryRequests.set(path, request)
    if (!silent) setLoading(true)
    try {
      const nodes = sortNodes(await backend.workspaceFiles(path))
      if (directoryRequests.get(path) !== request) return
      setChildren((current) => ({ ...current, [path]: nodes }))
      setError("")
    } catch (cause: any) {
      if (directoryRequests.get(path) !== request) return
      setError(cause?.message ?? "工作目录加载失败")
    } finally {
      if (path === "" && directoryRequests.get(path) === request) setLoading(false)
    }
  }

  const refresh = async (silent = false) => {
    await loadDirectory("", silent)
    const paths = Object.keys(expanded()).filter((path) => expanded()[path])
    await Promise.all(paths.map((path) => loadDirectory(path, true)))
  }

  const initialize = async () => {
    try {
      const workspace = await backend.workspace()
      setDirectory(workspace.directory)
      await refresh()
    } catch (cause: any) {
      setError(cause?.message ?? "工作目录初始化失败")
      setLoading(false)
    }
  }

  onMount(() => {
    void initialize()
    const timer = window.setInterval(() => {
      if (props.visible) void refresh(true)
    }, 5000)
    onCleanup(() => window.clearInterval(timer))
  })

  let wasVisible = props.visible
  createEffect(() => {
    const visible = props.visible
    if (visible && !wasVisible) void refresh(true)
    wasVisible = visible
  })

  let lastRefresh = props.refreshVersion
  createEffect(() => {
    const version = props.refreshVersion
    if (version === lastRefresh) return
    lastRefresh = version
    void refresh(true)
  })

  const toggleDirectory = async (path: string) => {
    const next = !expanded()[path]
    setExpanded((current) => ({ ...current, [path]: next }))
    if (next && !children()[path]) await loadDirectory(path, true)
  }

  const openFile = async (path: string) => {
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
    <>
      <Show when={props.open}>
        <div class="chat-workspace-backdrop" onClick={props.onClose} />
      </Show>
      <aside
        class="chat-workspace-panel"
        classList={{ collapsed: props.collapsed, open: props.open }}
        aria-label="工作目录"
      >
        <div class="chat-workspace-header">
          <div class="chat-workspace-heading">
            <strong>工作目录</strong>
            <span title={directory()}>{directoryName()}</span>
          </div>
          <div class="chat-workspace-header-actions">
            <button
              type="button"
              onClick={() => void downloadArchive()}
              disabled={!!downloading()}
              aria-label="下载工作目录 ZIP"
              title="下载工作目录 ZIP"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M5 19h14"/></svg>
            </button>
            <button type="button" onClick={() => void refresh()} aria-label="刷新工作目录" title="刷新工作目录">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 9a7 7 0 0 1 11.7-2L20 9M4 15l2.2 2a7 7 0 0 0 11.7-2"/></svg>
            </button>
            <button type="button" onClick={props.onCollapse} aria-label="收起工作目录" title="收起工作目录">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 18 6-6-6-6"/></svg>
            </button>
          </div>
        </div>

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
                  depth={0}
                  onToggle={toggleDirectory}
                  onOpen={openFile}
                  onDownload={downloadFile}
                  onDelete={deleteEntry}
                  downloading={downloading()}
                  deleting={deleting()}
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
      </aside>
    </>
  )
}

function WorkspaceTree(props: {
  nodes: WorkspaceNode[]
  children: Record<string, WorkspaceNode[]>
  expanded: Record<string, boolean>
  depth: number
  onToggle: (path: string) => void
  onOpen: (path: string) => void
  onDownload: (path: string) => void
  onDelete: (node: WorkspaceNode) => void
  downloading: string
  deleting: string
}) {
  return (
    <For each={props.nodes}>
      {(node) => (
        <>
          <div class="chat-workspace-node-row" style={{ "padding-left": `${8 + props.depth * 16}px` }}>
            <button
              type="button"
              class="chat-workspace-node"
              classList={{ "chat-workspace-node-directory": node.type === "directory" }}
              onClick={() => node.type === "directory" ? props.onToggle(node.path) : props.onOpen(node.path)}
              title={node.path}
            >
              <span class="chat-workspace-chevron">
                {node.type === "directory" ? (props.expanded[node.path] ? "▾" : "▸") : ""}
              </span>
              <span class="chat-workspace-node-icon">{node.type === "directory" ? "□" : "·"}</span>
              <span>{node.name}</span>
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
              depth={props.depth + 1}
              onToggle={props.onToggle}
              onOpen={props.onOpen}
              onDownload={props.onDownload}
              onDelete={props.onDelete}
              downloading={props.downloading}
              deleting={props.deleting}
            />
          </Show>
        </>
      )}
    </For>
  )
}
