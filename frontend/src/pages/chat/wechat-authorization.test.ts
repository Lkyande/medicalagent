import { describe, expect, test } from "bun:test"
import { waitForWechatAuthorization, wechatAuthorizationError } from "./wechat-authorization"

describe("waitForWechatAuthorization", () => {
  test("checks again after the authorization popup closes", async () => {
    let time = 0
    let checks = 0

    const result = await waitForWechatAuthorization({
      check: async () => ({ authorized: ++checks >= 2, authorizerAppid: checks >= 2 ? "wx-app" : null }),
      popupClosed: () => true,
      disposed: () => false,
      now: () => time,
      sleep: async (duration) => {
        time += duration
      },
    })

    expect(result).toEqual({ authorized: true, authorizerAppid: "wx-app" })
    expect(checks).toBe(2)
  })

  test("reports an incomplete authorization after the close grace period", async () => {
    let time = 0
    let checks = 0

    const result = waitForWechatAuthorization({
      check: async () => {
        checks += 1
        return { authorized: false, status: null }
      },
      popupClosed: () => true,
      disposed: () => false,
      now: () => time,
      sleep: async (duration) => {
        time += duration
      },
      popupCloseGraceMs: 3000,
    })

    await expect(result).rejects.toThrow("授权窗口已关闭，公众号授权未完成")
    expect(checks).toBe(3)
  })

  test("identifies a missing callback when the attempt remains pending", async () => {
    let time = 0
    const result = waitForWechatAuthorization({
      check: async () => ({ authorized: false, status: "pending" }),
      popupClosed: () => true,
      disposed: () => false,
      now: () => time,
      sleep: async (duration) => {
        time += duration
      },
      popupCloseGraceMs: 1500,
    })

    await expect(result).rejects.toThrow("服务端未收到微信授权回调，请检查微信开放平台回调地址后重试")
  })

  test("reports the broker failure instead of treating it as a closed popup", async () => {
    const result = waitForWechatAuthorization({
      check: async () => ({
        authorized: false,
        status: "failed",
        error: "This official account is already bound to another subject",
      }),
      popupClosed: () => true,
      disposed: () => false,
    })

    await expect(result).rejects.toThrow("该公众号已绑定到其他平台账号，请先解绑后重试")
  })

  test("keeps an unknown broker error visible", () => {
    expect(wechatAuthorizationError("api_query_auth failed: invalid auth code")).toBe(
      "公众号授权失败：api_query_auth failed: invalid auth code",
    )
  })
})
