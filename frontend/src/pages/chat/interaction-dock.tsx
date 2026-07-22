import { For, Show, createEffect, createMemo, createSignal } from "solid-js"
import type { PermissionRequest, QuestionRequest } from "@opencode-ai/sdk/v2"
import { backend } from "@/api/backend"

export function ChatInteractionDock(props: {
  permission?: PermissionRequest
  question?: QuestionRequest
  onResolved: (requestID: string) => void
}) {
  const [responding, setResponding] = createSignal(false)
  const [error, setError] = createSignal("")
  const [answers, setAnswers] = createSignal<string[][]>([])
  const [customAnswers, setCustomAnswers] = createSignal<string[]>([])
  const requestID = createMemo(() => props.question?.id ?? props.permission?.id)

  createEffect(() => {
    requestID()
    setAnswers([])
    setCustomAnswers([])
    setError("")
  })

  const decidePermission = async (reply: "once" | "always" | "reject") => {
    const request = props.permission
    if (!request || responding()) return
    setResponding(true)
    setError("")
    try {
      await backend.replyPermission(request.id, reply)
      props.onResolved(request.id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "确认失败")
    } finally {
      setResponding(false)
    }
  }

  const toggle = (questionIndex: number, label: string, multiple: boolean) => {
    setAnswers((current) => {
      const next = current.map((answer) => [...answer])
      const selected = next[questionIndex] ?? []
      next[questionIndex] = multiple
        ? selected.includes(label) ? selected.filter((item) => item !== label) : [...selected, label]
        : [label]
      return next
    })
  }

  const submitQuestion = async () => {
    const request = props.question
    if (!request || responding()) return
    const value = request.questions.map((question, index) => {
      const custom = customAnswers()[index]?.trim()
      if (!custom) return answers()[index] ?? []
      return question.multiple ? [...(answers()[index] ?? []), custom] : [custom]
    })
    if (value.some((answer) => answer.length === 0)) {
      setError("请回答所有问题")
      return
    }
    setResponding(true)
    setError("")
    try {
      await backend.replyQuestion(request.id, value)
      props.onResolved(request.id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "提交回答失败")
    } finally {
      setResponding(false)
    }
  }

  const rejectQuestion = async () => {
    const request = props.question
    if (!request || responding()) return
    setResponding(true)
    try {
      await backend.rejectQuestion(request.id)
      props.onResolved(request.id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "取消失败")
    } finally {
      setResponding(false)
    }
  }

  return (
    <Show when={requestID()}>
      <section class="chat-interaction-dock" aria-live="polite">
        <Show when={props.permission}>
          {(request) => (
            <>
              <div class="chat-interaction-heading">
                <strong>需要确认</strong>
                <span>{request().permission === "doom_loop" ? "检测到重复工具调用，是否继续？" : `是否允许 ${request().permission}？`}</span>
              </div>
              <Show when={request().patterns.length > 0}>
                <div class="chat-interaction-patterns">{request().patterns.join(" · ")}</div>
              </Show>
              <div class="chat-interaction-actions">
                <button type="button" onClick={() => void decidePermission("reject")} disabled={responding()}>拒绝</button>
                <button type="button" onClick={() => void decidePermission("always")} disabled={responding()}>始终允许</button>
                <button type="button" class="primary" onClick={() => void decidePermission("once")} disabled={responding()}>允许一次</button>
              </div>
            </>
          )}
        </Show>
        <Show when={props.question}>
          {(request) => (
            <>
              <div class="chat-interaction-heading"><strong>请确认</strong><span>回答后将继续执行</span></div>
              <For each={request().questions}>
                {(question, index) => (
                  <fieldset class="chat-interaction-question">
                    <legend>{question.question}</legend>
                    <For each={question.options}>
                      {(option) => {
                        const selected = () => answers()[index()]?.includes(option.label) ?? false
                        return (
                          <button type="button" data-selected={selected()} onClick={() => toggle(index(), option.label, question.multiple === true)} disabled={responding()}>
                            <strong>{option.label}</strong>
                            <Show when={option.description}><span>{option.description}</span></Show>
                          </button>
                        )
                      }}
                    </For>
                    <input
                      class="chat-interaction-custom-answer"
                      type="text"
                      value={customAnswers()[index()] ?? ""}
                      placeholder="输入其他答案"
                      disabled={responding()}
                      onInput={(event) => {
                        const value = event.currentTarget.value
                        if (!question.multiple && value.trim()) {
                          setAnswers((current) => {
                            const next = current.map((answer) => [...answer])
                            next[index()] = []
                            return next
                          })
                        }
                        setCustomAnswers((current) => {
                          const next = [...current]
                          next[index()] = value
                          return next
                        })
                      }}
                    />
                  </fieldset>
                )}
              </For>
              <div class="chat-interaction-actions">
                <button type="button" onClick={() => void rejectQuestion()} disabled={responding()}>取消</button>
                <button type="button" class="primary" onClick={() => void submitQuestion()} disabled={responding()}>{responding() ? "提交中..." : "确认并继续"}</button>
              </div>
            </>
          )}
        </Show>
        <Show when={error()}><div class="chat-error" role="alert">{error()}</div></Show>
      </section>
    </Show>
  )
}
