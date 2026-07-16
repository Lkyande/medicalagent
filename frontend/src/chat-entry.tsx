import { render } from "solid-js/web"
import { Show, createSignal, onMount, createMemo, For, onCleanup } from "solid-js"
import { LoginPage } from "@/pages/chat/login"
import { getChatClient, isLoggedIn, setToken } from "@/pages/chat/chat-client"
import "@/pages/chat/chat.css"

type SessionItem = { id: string; title: string; time?: number }
type DisplayMessage = { id: string; role: string; text: string; time: number }
type ModelInfo = { id: string; name: string; provider: string }

// --------------- model store ---------------
let _allModels: ModelInfo[] = []

async function loadAllModels() {
  if (_allModels.length > 0) return _allModels
  try {
    const resp = await getChatClient().provider.list()
    const body = (resp as any).data ?? resp
    const providers = body?.all ?? body ?? []
    const connected = new Set((body?.connected ?? []) as string[])
    const filtered = connected.size > 0 ? providers.filter((p: any) => connected.has(p.id ?? p.name ?? "")) : providers
    const all: ModelInfo[] = []
    for (const p of filtered) {
      const pid = p.id ?? p.name ?? ""
      for (const m of p.models ?? []) {
        const mid = typeof m === "string" ? m : m.id ?? m.name ?? ""
        if (mid) all.push({ id: pid + "/" + mid, name: mid, provider: pid })
      }
    }
    _allModels = all
  } catch (e) {
    console.error("loadModels error", e)
  }
  return _allModels
}

function getModels(): ModelInfo[] { return _allModels }

// --------------- components ---------------

function MessageBubble(props: { role: string; text: string }) {
  const isUser = props.role === "user"
  return (
    <div class="chat-bubble" classList={{ "chat-bubble-user": isUser, "chat-bubble-assistant": !isUser }}>
      <div style="white-space:pre-wrap;word-break:break-word">{props.text}</div>
    </div>
  )
}

function Sidebar(props: {
  sessions: SessionItem[]; loading: boolean; currentId: string; onDelete: (id: string) => void
}) {
  const [collapsedEarlier, setCollapsedEarlier] = createSignal(true)
  const groups = createMemo(() => {
    const now = Date.now()
    const d = new Date(); d.setHours(0, 0, 0, 0)
    const today = d.getTime(); const yesterday = today - 86400000
    const td: SessionItem[] = []; const yd: SessionItem[] = []; const er: SessionItem[] = []
    for (const s of props.sessions) { const t = s.time ?? 0; if (t >= today) td.push(s); else if (t >= yesterday) yd.push(s); else er.push(s) }
    const srt = (a: SessionItem, b: SessionItem) => (b.time ?? 0) - (a.time ?? 0)
    const r: { label: string; items: SessionItem[] }[] = []
    if (td.length) r.push({ label: "今天", items: td.sort(srt) })
    if (yd.length) r.push({ label: "昨天", items: yd.sort(srt) })
    if (er.length) r.push({ label: "更早", items: er.sort(srt) })
    return r
  })

  return (
    <div class="chat-sidebar">
      <div class="chat-sidebar-header">
        <a href="/chat" class="chat-new-btn" style="text-decoration:none;justify-content:center"><span style="font-size:16px">+</span> 新建对话</a>
      </div>
      <div class="chat-session-list">
        <Show when={props.loading && props.sessions.length === 0}><div style="padding:12px;color:#999;font-size:13px">加载中...</div></Show>
        <Show when={!props.loading && props.sessions.length === 0}><div style="padding:24px 12px;color:#999;font-size:13px;text-align:center">暂无会话</div></Show>
        <For each={groups()}>{group => {
          const isEarlier = group.label === "更早"
          return (<div>
            <div class="chat-session-group-label" style={isEarlier ? "cursor:pointer;user-select:none" : ""} onClick={() => isEarlier && setCollapsedEarlier(!collapsedEarlier())}>{isEarlier ? (collapsedEarlier() ? "▸ " : "▾ ") : ""}{group.label}{isEarlier ? " (" + group.items.length + ")" : ""}</div>
            <Show when={!isEarlier || !collapsedEarlier()}>
              <For each={group.items}>{item => {
                const d = new Date(item.time ?? 0)
                const ts = (d.getMonth()+1)+"/"+d.getDate()+" "+String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0")
                return (
                  <div style="display:flex;align-items:center;gap:4px">
                    <a href={"/chat/"+item.id} class="chat-session-item" classList={{active:item.id===props.currentId}} style="flex:1;min-width:0">
                      <span class="chat-session-item-title">{item.title}</span>
                      <span class="chat-session-item-time">{ts}</span>
                    </a>
                    <button onClick={e=>{e.preventDefault();e.stopPropagation();if(confirm("确认删除此会话？"))props.onDelete(item.id)}} style="width:24px;height:24px;border:none;background:transparent;cursor:pointer;color:#999;border-radius:4px;font-size:12px;display:flex;align-items:center;justify-content:center;flex-shrink:0" title="删除会话">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14"/></svg>
                    </button>
                  </div>
                )
              }}</For>
            </Show>
          </div>)
        }}</For>
      </div>
      <button style="margin:12px;padding:8px 12px;border:1px solid var(--chat-border);border-radius:8px;background:transparent;cursor:pointer;color:#999;font-size:13px" onClick={()=>{setToken(null);window.location.reload()}}>退出登录</button>
    </div>
  )
}

function ComposerToolbar(props: { agentMode: () => string; setAgentMode: (v: string) => void; selectedModel: () => string; setSelectedModel: (v: string) => void }) {
  const [modelOpen, setModelOpen] = createSignal(false)
  const models = getModels()
  let ddRef!: HTMLDivElement

  onMount(() => {
    const h = (e: MouseEvent) => { if (ddRef && !ddRef.contains(e.target as Node)) setModelOpen(false) }
    document.addEventListener("click", h)
    onCleanup(() => document.removeEventListener("click", h))
  })

  return (
    <div style="display:flex;align-items:center;gap:8px">
      <button onClick={() => props.setAgentMode(props.agentMode() === "build" ? "plan" : "build")} style="padding:5px 10px;border:1px solid var(--chat-border);border-radius:8px;background:transparent;cursor:pointer;color:var(--chat-text);font-size:13px" title={props.agentMode()==="build"?"构建模式":"计划模式"}>
        {props.agentMode() === "build" ? "构建" : "计划"}
      </button>
      <div style="position:relative" ref={ddRef}>
        <button onClick={() => setModelOpen(!modelOpen())} style="padding:5px 10px;border:1px solid var(--chat-border);border-radius:8px;background:transparent;cursor:pointer;color:var(--chat-text);font-size:13px;display:flex;align-items:center;gap:4px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
          {(() => { const m = models.find(x => x.id === props.selectedModel()); return m ? m.name : (props.selectedModel() || "选择模型") })()}
        </button>
        <Show when={modelOpen()}>
          <div class="chat-model-dropdown" style="bottom:auto;top:100%;margin-top:4px">
            <For each={models}>{m => (
              <div class="chat-model-option" classList={{active:m.id===props.selectedModel()}} onClick={()=>{props.setSelectedModel(m.id);setModelOpen(false)}}>
                <span class="chat-model-option-name">{m.name}</span>
                <span class="chat-model-option-provider">{m.provider}</span>
              </div>
            )}</For>
          </div>
        </Show>
      </div>
    </div>
  )
}

// --------------- pages ---------------

function ChatView(props: {
  sessions: SessionItem[]; loading: boolean; onDelete: (id: string) => void
  agentMode: () => string; setAgentMode: (v: string) => void; selectedModel: () => string; setSelectedModel: (v: string) => void
}) {
  let inputRef!: HTMLTextAreaElement
  const [sending, setSending] = createSignal(false); const [error, setError] = createSignal("")

  const send = async () => {
    const text = inputRef.value.trim()
    if (!text || sending()) return
    setSending(true); setError(""); inputRef.disabled = true
    try {
      const client = getChatClient()
      const r = await client.session.create()
      const sid = (r as any).data?.id; if (!sid) throw new Error("创建失败")
      const title = text.length > 40 ? text.slice(0, 40) + "..." : text
      getChatClient().session.update({ sessionID: sid, title }).catch(() => {})
      await client.session.promptAsync({ sessionID: sid, agent: props.agentMode(), model: props.selectedModel() || undefined, parts: [{ type: "text", text }] } as any)
      window.location.href = "/chat/" + sid
    } catch (err: any) { setError(err?.message ?? "发送失败"); inputRef.disabled = false }
    finally { setSending(false) }
  }

  return (
    <div class="chat-layout">
      <Sidebar sessions={props.sessions} loading={props.loading} currentId="" onDelete={props.onDelete} />
      <div class="chat-main">
        <div class="chat-header"><div class="chat-header-title">新对话</div></div>
        <div class="chat-messages"><div class="chat-messages-inner">
          <div class="chat-empty"><h2>开新对话</h2><p style="margin:0">输入消息开始与 AI 助手对话</p></div>
          <Show when={error()}><div class="chat-error">{error()}</div></Show>
        </div></div>
        <div class="chat-composer"><div class="chat-composer-inner">
          <textarea ref={inputRef} class="chat-composer-input" disabled={sending()} placeholder="输入消息..." onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send() } }} />
          <div class="chat-composer-toolbar">
            <ComposerToolbar agentMode={props.agentMode} setAgentMode={props.setAgentMode} selectedModel={props.selectedModel} setSelectedModel={props.setSelectedModel} />
            <button class="chat-send-btn" disabled={sending()} onClick={send}>{sending() ? "..." : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>}</button>
          </div>
        </div></div>
      </div>
    </div>
  )
}

function SessionView(props: {
  sessions: SessionItem[]; loading: boolean; onDelete: (id: string) => void
  agentMode: () => string; setAgentMode: (v: string) => void; selectedModel: () => string; setSelectedModel: (v: string) => void
}) {
  const id = (() => { const m = location.pathname.match(/chat\/(.+)/); return m ? m[1] : "" })()
  const [msgs, setMsgs] = createSignal<any[]>([]); const [loading, setLoading] = createSignal(true)
  const [status, setStatus] = createSignal(""); const [sending, setSending] = createSignal(false); const [error, setError] = createSignal("")
  let inputRef!: HTMLTextAreaElement; let msgBoxRef!: HTMLDivElement; let timer: any

  const load = async () => {
    try {
      const r = await getChatClient().session.messages({ sessionID: id })
      const d = (r as any).data ?? []
      if (d.length > 0) console.log("msgs:", d.length, "last role:", d[d.length-1]?.info?.role)
      setMsgs(Array.isArray(d) ? d : [])
      try { const st = await getChatClient().session.status(); const list = (st as any).data ?? st ?? {}; const e = Array.isArray(list) ? list.find((s: any) => s.id === id) : list[id]; setStatus(e?.type ?? "") } catch {}
    } catch (e) { console.error("load msgs error", e) }
    finally { setLoading(false) }
  }

  onMount(() => {
    load()
    const run = () => { timer = setTimeout(() => { load(); run() }, status() === "busy" ? 1000 : 3000) }
    run()
    return () => clearTimeout(timer)
  })

  createMemo(() => { msgs(); setTimeout(() => { if (msgBoxRef) msgBoxRef.scrollTop = msgBoxRef.scrollHeight }, 150) })

  const send = async () => {
    const text = inputRef.value.trim(); if (!text || sending()) return
    setSending(true); setError(""); const el = inputRef; el.disabled = true
    try {
      await getChatClient().session.promptAsync({ sessionID: id, agent: props.agentMode(), model: props.selectedModel() || undefined, parts: [{ type: "text", text }] } as any)
      el.value = ""; setTimeout(() => load(), 600)
    } catch (err: any) { setError(err?.message ?? "发送失败"); el.disabled = false }
    finally { setSending(false) }
  }

  const parts = createMemo((): DisplayMessage[] => msgs().map((m: any) => ({
    id: m.info?.id ?? m.id ?? "", role: m.info?.role ?? "user",
    text: (m.parts ?? []).filter((p: any) => p.type === "text").map((p: any) => p.text ?? "").join("\n") || "(空)",
    time: m.info?.time?.created ?? 0,
  })))

  const del = async () => { if (confirm("确认删除？")) { await getChatClient().session.delete({ sessionID: id }); window.location.href = "/chat" } }

  return (
    <div class="chat-layout">
      <Sidebar sessions={props.sessions} loading={props.loading} currentId={id} onDelete={props.onDelete} />
      <div class="chat-main">
        <div class="chat-header">
          <div class="chat-header-title">会话 {id.slice(0,16)}...</div>
          <div style="display:flex;align-items:center;gap:8px">
            <Show when={status() === "busy"}><span style="font-size:12px;color:#06f">生成中...</span></Show>
            <button onClick={del} style="padding:6px 12px;border:1px solid var(--chat-border);border-radius:8px;background:transparent;cursor:pointer;color:#c00;font-size:13px">删除会话</button>
          </div>
        </div>
        <div class="chat-messages" ref={msgBoxRef}><div class="chat-messages-inner">
          <Show when={loading()}><div class="chat-loading">加载中...</div></Show>
          <Show when={!loading() && parts().length === 0}><div class="chat-empty"><h2>暂无消息</h2></div></Show>
          <For each={parts()}>{p => <MessageBubble role={p.role} text={p.text} />}</For>
          <Show when={status() === "busy" && parts().length > 0}><div class="chat-typing-indicator"><div class="chat-typing-dot"/><div class="chat-typing-dot"/><div class="chat-typing-dot"/></div></Show>
          <Show when={error()}><div class="chat-error">{error()}</div></Show>
        </div></div>
        <div class="chat-composer"><div class="chat-composer-inner">
          <textarea ref={inputRef} class="chat-composer-input" placeholder="继续对话..." onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send() } }} />
          <div class="chat-composer-toolbar">
            <ComposerToolbar agentMode={props.agentMode} setAgentMode={props.setAgentMode} selectedModel={props.selectedModel} setSelectedModel={props.setSelectedModel} />
            <button class="chat-send-btn" disabled={sending()} onClick={send}>{sending() ? "..." : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>}</button>
          </div>
        </div></div>
      </div>
    </div>
  )
}

// --------------- root ---------------

function ChatPage() {
  const id = (() => { const m = location.pathname.match(/chat\/(.+)/); return m ? m[1] : "" })()
  const [sessions, setSessions] = createSignal<SessionItem[]>([])
  const [sLoading, setSLoading] = createSignal(true)
  const [agentMode, setAgentMode] = createSignal<"build" | "plan">("build")
  const [selectedModel, setSelectedModel] = createSignal("")

  onMount(() => {
    loadAllModels().then(() => {
      if (!selectedModel()) { const m = getModels(); if (m.length > 0) setSelectedModel(m[0].id) }
      console.log("models:", getModels().length, "sel:", selectedModel())
    }).catch(e => console.error("loadModels err", e))
    loadSessions()
    setInterval(() => loadSessions(true), 10000)
  })

  const loadSessions = async (silent = false) => {
    if (!silent) setSLoading(true)
    try {
      const r = await getChatClient().session.list()
      const d = (r as any).data ?? []
      setSessions(d.map((s: any) => ({ id: s.id, title: s.title || "新会话", time: s.time?.updated ?? s.time?.created ?? 0 })))
    } catch (e) { console.error("load sessions err", e) }
    finally { setSLoading(false) }
  }

  const del = async (sid: string) => {
    try { await getChatClient().session.delete({ sessionID: sid }); setSessions(p => p.filter(s => s.id !== sid)); if (sid === id) window.location.href = "/chat" }
    catch (e: any) { alert("删除失败: " + (e?.message ?? String(e))) }
  }

  const sp = { sessions: sessions as () => SessionItem[], loading: sLoading as () => boolean, onDelete: del, agentMode: agentMode as () => string, setAgentMode, selectedModel: selectedModel as () => string, setSelectedModel }
  return id ? <SessionView {...sp} /> : <ChatView {...sp} />
}

function App() {
  const [loggedIn, setLoggedIn] = createSignal(isLoggedIn())
  onMount(() => { document.body.setAttribute("data-page", "chat") })
  return (
    <Show when={loggedIn()} fallback={<LoginPage onLogin={() => setLoggedIn(true)} />}>
      <ChatPage />
    </Show>
  )
}

if (typeof window !== "undefined") {
  const root = document.getElementById("root")
  if (root instanceof HTMLElement) render(() => <App />, root)
}