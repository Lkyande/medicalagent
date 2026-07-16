import { createSignal, Show } from "solid-js"
import { backend } from "@/api/backend"
import { setToken } from "./chat-client"

export function LoginPage(props: { onLogin: () => void }) {
  const [mode, setMode] = createSignal<"login" | "register">("login")
  const [email, setEmail] = createSignal("")
  const [password, setPassword] = createSignal("")
  const [error, setError] = createSignal("")
  const [loading, setLoading] = createSignal(false)

  const handleSubmit = async (e: Event) => {
    e.preventDefault()
    setError("")
    setLoading(true)
    try {
      const request = mode() === "login" ? backend.login : backend.register
      const data = await request(email(), password())
      setToken(data.token)
      props.onLogin()
      window.location.reload()
    } catch (e: any) {
      setError(e?.message ?? "操作失败")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        display: "flex",
        "align-items": "center",
        "justify-content": "center",
        height: "100vh",
        background: "var(--v2-background-bg-deep, #fafafa)",
      }}
    >
      <div
        style={{
          width: "360px",
          padding: "32px",
          "border-radius": "16px",
          background: "var(--v2-surface-surface-raised, #fff)",
          border: "1px solid var(--v2-border-border, #e5e5e5)",
          "box-shadow": "0 2px 12px rgba(0,0,0,0.06)",
        }}
      >
        <h1
          style={{
            "font-size": "24px",
            "font-weight": "600",
            margin: "0 0 8px",
            "text-align": "center",
          }}
        >
          AI Chat
        </h1>
        <p
          style={{
            "font-size": "14px",
            color: "#666",
            margin: "0 0 24px",
            "text-align": "center",
          }}
        >
          {mode() === "login" ? "登录到你的账户" : "创建新账户"}
        </p>

        <form onSubmit={handleSubmit} style={{ display: "flex", "flex-direction": "column", gap: "12px" }}>
          <input
            type="email"
            placeholder="邮箱"
            value={email()}
            onInput={(e) => setEmail(e.currentTarget.value)}
            required
            style={{
              padding: "12px 16px",
              border: "1px solid #e5e5e5",
              "border-radius": "10px",
              "font-size": "15px",
              outline: "none",
              background: "#fff",
              color: "#1a1a1a",
            }}
          />
          <input
            type="password"
            placeholder="密码"
            value={password()}
            onInput={(e) => setPassword(e.currentTarget.value)}
            required
            style={{
              padding: "12px 16px",
              border: "1px solid #e5e5e5",
              "border-radius": "10px",
              "font-size": "15px",
              outline: "none",
              background: "#fff",
              color: "#1a1a1a",
            }}
          />
          <Show when={error()}>
            <div style={{ color: "#c00", "font-size": "13px", padding: "8px 0" }}>{error()}</div>
          </Show>
          <button
            type="submit"
            disabled={loading()}
            style={{
              padding: "12px",
              border: "none",
              "border-radius": "10px",
              background: "#0066ff",
              color: "white",
              "font-size": "15px",
              cursor: loading() ? "not-allowed" : "pointer",
              opacity: loading() ? 0.6 : 1,
            }}
          >
            {loading() ? "处理中..." : mode() === "login" ? "登录" : "注册"}
          </button>
        </form>

        <button
          onClick={() => setMode(mode() === "login" ? "register" : "login")}
          style={{
            width: "100%",
            margin: "16px 0 0",
            padding: "10px",
            border: "none",
            background: "transparent",
            color: "#0066ff",
            "font-size": "14px",
            cursor: "pointer",
            "text-align": "center",
          }}
        >
          {mode() === "login" ? "没有账户？去注册" : "已有账户？去登录"}
        </button>
      </div>
    </div>
  )
}
