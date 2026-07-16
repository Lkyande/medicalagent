import { describe, expect, test } from "bun:test"
import { DEFAULT_CHAT_MODEL, defaultChatModel } from "./model-selector"

describe("defaultChatModel", () => {
  test("prefers OpenAI GPT-5.5", () => {
    expect(defaultChatModel([
      { id: "openai/o3", provider: "OpenAI", name: "o3" },
      { id: DEFAULT_CHAT_MODEL, provider: "OpenAI", name: "GPT-5.5" },
    ])).toBe(DEFAULT_CHAT_MODEL)
  })

  test("falls back to the first connected model", () => {
    expect(defaultChatModel([
      { id: "openai/o3", provider: "OpenAI", name: "o3" },
    ])).toBe("openai/o3")
  })
})
