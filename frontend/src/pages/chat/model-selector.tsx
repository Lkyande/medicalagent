import { Show, For, createSignal, onMount, onCleanup } from "solid-js"
import { getChatClient } from "./chat-client"

interface ModelInfo {
  id: string
  provider: string
  name: string
}

export const DEFAULT_CHAT_MODEL = "openai/gpt-5.5"

export function defaultChatModel(models: ModelInfo[]) {
  return models.find((model) => model.id === DEFAULT_CHAT_MODEL)?.id ?? models[0]?.id
}

export function ChatModelSelector(props: {
  selected: string
  onSelect: (model: string) => void
}) {
  const [open, setOpen] = createSignal(false)
  const [models, setModels] = createSignal<ModelInfo[]>([])
  const [loading, setLoading] = createSignal(true)
  const [error, setError] = createSignal("")
  let ref: HTMLDivElement | undefined

  onMount(async () => {
    try {
      const resp = await getChatClient().provider.list()
      const data = (resp as any).data ?? resp
      const providers = data?.all ?? data ?? []
      const connected = new Set((data?.connected ?? []) as string[])
      const filtered =
        connected.size > 0
          ? providers.filter((p: any) => connected.has(p.id ?? p.name ?? ""))
          : providers
      const allModels: ModelInfo[] = []
      for (const p of filtered) {
        const providerId = p.id ?? p.name ?? ""
        const providerModels = p.models ?? {}
        const entries = Array.isArray(providerModels)
          ? providerModels.map((model: any) => [typeof model === "string" ? model : model.id ?? model.name, model] as const)
          : Object.entries(providerModels)
        for (const [key, m] of entries) {
          const model = m as any
          const modelId = typeof model === "string" ? model : model?.id ?? key
          if (modelId) {
            allModels.push({
              id: `${providerId}/${modelId}`,
              provider: p.name ?? providerId,
              name: typeof model === "string" ? model : model?.name ?? modelId,
            })
          }
        }
      }
      setModels(allModels)
      if (!props.selected) {
        const fallback = defaultChatModel(allModels)
        if (fallback) props.onSelect(fallback)
      }
    } catch (e) {
      console.error("Failed to load models", e)
      setError(e instanceof Error ? e.message : "模型加载失败")
    } finally {
      setLoading(false)
    }

    const handler = (e: MouseEvent) => {
      if (ref && !ref.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("click", handler)
    onCleanup(() => document.removeEventListener("click", handler))
  })

  const displayName = () => {
    if (!props.selected) return "选择模型"
    const m = models().find((m) => m.id === props.selected)
    return m?.name ?? props.selected
  }

  return (
    <div class="chat-model-selector" ref={ref}>
      <button type="button" class="chat-model-btn" onClick={() => setOpen(!open())} aria-expanded={open()} aria-haspopup="listbox">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
        </svg>
        <span class="chat-model-btn-label">{displayName()}</span>
      </button>
      <Show when={open()}>
        <div class="chat-model-dropdown" role="listbox" aria-label="选择模型">
          <For each={models()}>
            {(m) => (
              <button
                type="button"
                class="chat-model-option"
                classList={{ active: m.id === props.selected }}
                role="option"
                aria-selected={m.id === props.selected}
                onClick={() => {
                  props.onSelect(m.id)
                  setOpen(false)
                }}
              >
                <span class="chat-model-option-name">{m.name}</span>
                <span class="chat-model-option-provider">{m.provider}</span>
              </button>
            )}
          </For>
          <Show when={models().length === 0}>
            <div class="chat-model-empty">{loading() ? "加载中..." : error() || "没有可用模型"}</div>
          </Show>
        </div>
      </Show>
    </div>
  )
}
