import { createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js"
import type { Agent } from "@opencode-ai/sdk/v2"
import { getChatClient } from "./chat-client"

const PRIMARY_AGENTS = ["build", "plan"]

const labels: Record<string, string> = {
  build: "Build",
  plan: "Plan",
}

export function ChatAssistantSelector(props: {
  selected: string
  onSelect: (agent: string) => void
}) {
  const [open, setOpen] = createSignal(false)
  const [agents, setAgents] = createSignal<Agent[]>([])
  const [error, setError] = createSignal("")
  let ref: HTMLDivElement | undefined

  const available = createMemo(() =>
    agents()
      .filter((agent) => !agent.hidden && agent.mode !== "subagent" && PRIMARY_AGENTS.includes(agent.name))
      .sort((a, b) => PRIMARY_AGENTS.indexOf(a.name) - PRIMARY_AGENTS.indexOf(b.name)),
  )

  onMount(async () => {
    try {
      const response = await getChatClient().app.agents()
      const data = (response as { data?: Agent[] }).data ?? response
      const list = Array.isArray(data) ? data : []
      setAgents(list)
      const first = list.find((agent) => !agent.hidden && agent.mode !== "subagent" && agent.name === "build")
        ?? list.find((agent) => !agent.hidden && agent.mode !== "subagent" && agent.name === "plan")
      if (!props.selected && first) props.onSelect(first.name)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法加载 OpenCode Agent")
    }

    const close = (event: MouseEvent) => {
      if (ref && !ref.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener("click", close)
    onCleanup(() => document.removeEventListener("click", close))
  })

  const current = createMemo(() => available().find((agent) => agent.name === props.selected))

  return (
    <div class="chat-assistant-selector" ref={ref}>
      <button
        type="button"
        class="chat-assistant-btn"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open()}
        aria-haspopup="listbox"
      >
        <span class="chat-assistant-indicator" />
        <span>{current() ? labels[current()!.name] ?? current()!.name : labels[props.selected] ?? "选择 Agent"}</span>
      </button>
      <Show when={open()}>
        <div class="chat-assistant-dropdown" role="listbox" aria-label="选择 Agent">
          <For each={available()}>
            {(agent) => (
              <button
                type="button"
                class="chat-assistant-option"
                classList={{ active: agent.name === props.selected }}
                role="option"
                aria-selected={agent.name === props.selected}
                onClick={() => {
                  props.onSelect(agent.name)
                  setOpen(false)
                }}
              >
                <span class="chat-assistant-option-name">{labels[agent.name] ?? agent.name}</span>
                <Show when={agent.description}>
                  <span class="chat-assistant-option-description">{agent.description}</span>
                </Show>
              </button>
            )}
          </For>
          <Show when={available().length === 0}>
            <div class="chat-assistant-empty">{error() || "OpenCode Server 未返回 Build 或 Plan Agent"}</div>
          </Show>
        </div>
      </Show>
    </div>
  )
}
