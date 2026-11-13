const POLL_INTERVAL_MS = 1500
const AUTHORIZATION_TIMEOUT_MS = 10 * 60 * 1000
const POPUP_CLOSE_GRACE_MS = 6000

type AuthorizationStatus = {
  authorized: boolean
  status?: string | null
  error?: string | null
}

export function wechatAuthorizationError(error?: string | null): string {
  const message = error?.trim() ?? ""
  const lower = message.toLowerCase()
  if (lower.includes("already bound to another subject")) {
    return "该公众号已绑定到其他平台账号，请先解绑后重试"
  }
  if (lower.includes("invalid, expired, or already used") || lower.includes("state was already used")) {
    return "本次公众号授权链接已失效，请重新发起授权"
  }
  if (lower.includes("did not grant required function")) return "公众号未授予所需权限，请重新授权并勾选全部权限"
  return message ? `公众号授权失败：${message}` : "公众号授权失败，请重新操作"
}

export async function waitForWechatAuthorization<T extends AuthorizationStatus>(options: {
  check: () => Promise<T>
  popupClosed: () => boolean
  disposed: () => boolean
  now?: () => number
  sleep?: (duration: number) => Promise<void>
  timeoutMs?: number
  popupCloseGraceMs?: number
}): Promise<T> {
  const now = options.now ?? Date.now
  const sleep = options.sleep ?? ((duration) => new Promise((resolve) => globalThis.setTimeout(resolve, duration)))
  const deadline = now() + (options.timeoutMs ?? AUTHORIZATION_TIMEOUT_MS)
  let popupCloseDeadline: number | undefined

  while (!options.disposed() && now() < deadline) {
    const authorization = await options.check()
    if (authorization.authorized) return authorization
    if (authorization.status === "failed") throw new Error(wechatAuthorizationError(authorization.error))
    if (authorization.status === "expired") throw new Error("本次公众号授权已过期，请重新操作")

    const checkedAt = now()
    if (options.popupClosed()) {
      popupCloseDeadline ??= Math.min(
        deadline,
        checkedAt + (options.popupCloseGraceMs ?? POPUP_CLOSE_GRACE_MS),
      )
      if (checkedAt >= popupCloseDeadline) {
        if (authorization.status === "pending") {
          throw new Error("服务端未收到微信授权回调，请检查微信开放平台回调地址后重试")
        }
        if (authorization.status === "processing") {
          throw new Error("公众号授权回调处理未完成，请稍后重试")
        }
        throw new Error("授权窗口已关闭，公众号授权未完成")
      }
    }

    const nextDeadline = popupCloseDeadline ?? deadline
    await sleep(Math.min(POLL_INTERVAL_MS, Math.max(0, nextDeadline - now())))
  }

  throw new Error("公众号授权等待超时，请重新操作")
}
