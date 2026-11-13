import { Show } from "solid-js"

export type ChatStartupPhase = "identity" | "capabilities"

export function ChatStartupScreen(props: {
  phase: ChatStartupPhase
  error?: string
  onRetry: () => void
}) {
  const capabilities = () => props.phase === "capabilities"

  return (
    <main
      class="chat-startup-screen"
      classList={{
        "chat-startup-capabilities": capabilities(),
        "chat-startup-failed": !!props.error,
      }}
    >
      <div class="chat-startup-atmosphere" aria-hidden="true" />
      <section
        class="chat-startup-panel"
        role={props.error ? "alert" : "status"}
        aria-live="polite"
        aria-busy={!props.error}
      >
        <div class="chat-startup-hub" aria-hidden="true">
          <svg class="chat-startup-links" viewBox="0 0 360 320" fill="none">
            <g class="chat-startup-link chat-startup-link-top">
              <line class="chat-startup-link-base" x1="180" y1="160" x2="180" y2="58" />
              <line class="chat-startup-link-signal" pathLength="100" x1="180" y1="160" x2="180" y2="58" />
            </g>
            <g class="chat-startup-link chat-startup-link-right">
              <line class="chat-startup-link-base" x1="180" y1="160" x2="268" y2="160" />
              <line class="chat-startup-link-signal" pathLength="100" x1="180" y1="160" x2="268" y2="160" />
            </g>
            <g class="chat-startup-link chat-startup-link-bottom">
              <line class="chat-startup-link-base" x1="180" y1="160" x2="180" y2="262" />
              <line class="chat-startup-link-signal" pathLength="100" x1="180" y1="160" x2="180" y2="262" />
            </g>
            <g class="chat-startup-link chat-startup-link-left">
              <line class="chat-startup-link-base" x1="180" y1="160" x2="92" y2="160" />
              <line class="chat-startup-link-signal" pathLength="100" x1="180" y1="160" x2="92" y2="160" />
            </g>
          </svg>

          <div class="chat-startup-core">
            <span class="chat-startup-core-ring chat-startup-core-ring-outer" />
            <span class="chat-startup-core-ring chat-startup-core-ring-inner" />
            <span class="chat-startup-core-mark">{props.error ? "!" : "M"}</span>
          </div>

          <div class="chat-startup-node chat-startup-node-top">
            <span class="chat-startup-node-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M12 3 5 6v5c0 4.8 2.9 8.2 7 10 4.1-1.8 7-5.2 7-10V6l-7-3Z" />
                <path d="M9 11h6M12 8v6" />
              </svg>
            </span>
            <span>医学知识</span>
          </div>

          <div class="chat-startup-node chat-startup-node-right">
            <span class="chat-startup-node-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M4 18h16M6 15l3-4 3 2 5-7 2 2" />
                <circle cx="6" cy="15" r="1" />
                <circle cx="17" cy="6" r="1" />
              </svg>
            </span>
            <span>宣教热点</span>
          </div>

          <div class="chat-startup-node chat-startup-node-bottom">
            <span class="chat-startup-node-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M5 3h11l3 3v15H5z" />
                <path d="M16 3v4h4M8 11h8M8 15h6" />
              </svg>
            </span>
            <span>排版预览</span>
          </div>

          <div class="chat-startup-node chat-startup-node-left">
            <span class="chat-startup-node-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <rect x="3" y="5" width="18" height="14" rx="2" />
                <path d="m8 14 2.5-3 2 2 2.5-3 3 4M9 9h.01" />
              </svg>
            </span>
            <span>图像视频</span>
          </div>
        </div>

        <div class="chat-startup-copy">
          <span class="chat-startup-eyebrow">MEDICAL AGENT</span>
          <Show
            when={!props.error}
            fallback={
              <>
                <h1>初始化未完成</h1>
                <p>{props.error}</p>
                <button type="button" class="chat-startup-retry" onClick={props.onRetry}>
                  重新初始化
                </button>
              </>
            }
          >
            <h1>{capabilities() ? "正在连接医疗能力" : "正在确认你的身份"}</h1>
            <p>{capabilities() ? "准备医学知识、宣教热点、排版与媒体工具" : "建立安全会话并定位专属工作区"}</p>
            <div class="chat-startup-steps" aria-label={capabilities() ? "工作区能力初始化中" : "身份验证中"}>
              <span class="chat-startup-step chat-startup-step-identity" />
              <span class="chat-startup-step chat-startup-step-capabilities" />
            </div>
          </Show>
        </div>
      </section>
    </main>
  )
}
