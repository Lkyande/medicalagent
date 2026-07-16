import { describe, expect, test } from "bun:test"
import { createChatIdentifier } from "./identifier"

describe("createChatIdentifier", () => {
  test("uses the OpenCode wire format", () => {
    expect(createChatIdentifier("msg", 1_700_000_000_000)).toMatch(/^msg_[0-9a-f]{12}[0-9A-Za-z]{14}$/)
    expect(createChatIdentifier("prt", 1_700_000_000_001)).toMatch(/^prt_[0-9a-f]{12}[0-9A-Za-z]{14}$/)
  })

  test("keeps IDs ordered within the same millisecond", () => {
    const first = createChatIdentifier("msg", 1_700_000_000_002)
    const second = createChatIdentifier("msg", 1_700_000_000_002)

    expect(first < second).toBe(true)
  })

  test("keeps IDs ordered across milliseconds", () => {
    const first = createChatIdentifier("msg", 1_700_000_000_003)
    const second = createChatIdentifier("msg", 1_700_000_000_004)

    expect(first < second).toBe(true)
  })
})
