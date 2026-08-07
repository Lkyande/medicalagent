import { createMemo, createSignal, For, onCleanup, onMount, Show } from "solid-js"
import { backend, type PlatformAssistant } from "@/api/backend"

export const DEFAULT_ASSISTANT_ID = "build"
export const CLINICAL_ASSISTANT_ID = "clinical-assistant"
export const DISEASE_MANAGEMENT_ASSISTANT_ID = "disease-management-assistant"
export const EDUCATION_ASSISTANT_ID = "education-assistant"

export type AssistantOption = {
  id: string
  name: string
  description: string
  baseAgent: "build" | "plan"
  systemPrompt?: string
  platform?: PlatformAssistant
}

const BUILTIN_ASSISTANTS: AssistantOption[] = [
  {
    id: "build",
    name: "Build",
    description: "基础执行 Agent，负责开发、修改和工具调用。",
    baseAgent: "build",
  },
  {
    id: "plan",
    name: "Plan",
    description: "基础规划 Agent，只进行分析和方案设计。",
    baseAgent: "plan",
  },
]

export function assistantOptions(catalog: PlatformAssistant[], includePlatform = false): AssistantOption[] {
  if (!includePlatform) return BUILTIN_ASSISTANTS
  return [...BUILTIN_ASSISTANTS, ...catalog.map(platformAssistantOption)]
}

function platformAssistantOption(assistant: PlatformAssistant): AssistantOption {
  return {
    id: assistant.id,
    name: assistant.name,
    description: assistant.description,
    baseAgent: assistant.baseAgent,
    systemPrompt: assistant.systemPrompt,
    platform: assistant,
  }
}

export function resolveAssistant(catalog: PlatformAssistant[], selected: string, includePlatform = false): AssistantOption {
  return assistantOptions(catalog, includePlatform).find((assistant) => assistant.id === selected) ?? BUILTIN_ASSISTANTS[0]
}

export function ChatAssistantSelector(props: {
  selected: string
  onSelect: (assistant: AssistantOption) => void
}) {
  const [open, setOpen] = createSignal(false)
  const [catalog, setCatalog] = createSignal<PlatformAssistant[]>([])
  const [error, setError] = createSignal("")
  let ref: HTMLDivElement | undefined

  const options = createMemo(() => assistantOptions(catalog()))
  const current = createMemo(() => resolveAssistant(catalog(), props.selected))

  onMount(async () => {
    try {
      const platformAssistants = await backend.assistants()
      setCatalog(platformAssistants)
      const selected = resolveAssistant(platformAssistants, props.selected)
      if (selected.id !== props.selected) props.onSelect(selected)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法加载助手能力")
    }

    const close = (event: MouseEvent) => {
      if (ref && !ref.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener("click", close)
    onCleanup(() => document.removeEventListener("click", close))
  })

  const select = (assistant: AssistantOption) => {
    props.onSelect(assistant)
    setOpen(false)
  }

  return (
    <div class="chat-assistant-selector" ref={ref}>
      <button
        type="button"
        class="chat-assistant-btn"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open()}
        aria-haspopup="listbox"
        title="选择工作模式"
      >
        <span class="chat-assistant-indicator" />
        <span>{current().name}</span>
      </button>
      <Show when={open()}>
        <div class="chat-assistant-dropdown" role="listbox" aria-label="选择工作模式">
          <For each={options()}>
            {(assistant) => (
              <button
                type="button"
                class="chat-assistant-option"
                classList={{ active: assistant.id === props.selected }}
                role="option"
                aria-selected={assistant.id === props.selected}
                onClick={() => select(assistant)}
              >
                <span class="chat-assistant-option-row">
                  <span class="chat-assistant-option-name">{assistant.name}</span>
                  <Show when={assistant.platform}>
                    <span class="chat-assistant-install-action">已就绪</span>
                  </Show>
                </span>
                <span class="chat-assistant-option-description">{assistant.description}</span>
              </button>
            )}
          </For>
          <Show when={error()}>
            <div class="chat-assistant-error">{error()}</div>
          </Show>
        </div>
      </Show>
    </div>
  )
}
