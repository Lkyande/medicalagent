import { createOpencodeClient } from "@opencode-ai/sdk/v2/client"

let _client: any = null
let _token: string | null = null

const TOKEN_KEY = "chat_token"

export function getToken(): string | null {
  if (_token) return _token
  try {
    _token = localStorage.getItem(TOKEN_KEY)
  } catch {
    _token = null
  }
  return _token
}

export function setToken(token: string | null) {
  _token = token
  _client = null
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {}
}

export function isLoggedIn(): boolean {
  return !!getToken()
}

export function getChatClient() {
  if (!_client) {
    const baseUrl = import.meta.env.VITE_OPENCODE_PROXY_BASE ?? "/opencode"
    const token = getToken()
    const headers: Record<string, string> = {}
    if (token) headers["Authorization"] = `Bearer ${token}`
    _client = createOpencodeClient({ baseUrl, throwOnError: true, headers })
  }
  return _client
}