import { describe, expect, test } from "bun:test"
import type { Message, Part } from "@opencode-ai/sdk/v2"
import { appendPartDelta, filterRevertedMessages, mergeMessageSnapshot, normalizeMessages, upsertMessage, upsertPart, type RawMessage } from "./message-normalize"
import { serializeWorkspaceAttachment } from "./workspace-attachment"

const user = (id: string, created: number): RawMessage => ({
  info: {
    id,
    sessionID: "session",
    role: "user",
    time: { created },
    agent: "build",
    model: { providerID: "openai", modelID: "gpt-5.5" },
  } as Message,
  parts: [{ id: `${id}-text`, sessionID: "session", messageID: id, type: "text", text: "Question" } as Part],
})

const assistant = (
  id: string,
  parentID: string,
  created: number,
  completed: number,
  parts: Part[],
): RawMessage => ({
  info: {
    id,
    sessionID: "session",
    role: "assistant",
    parentID,
    time: { created, completed },
    modelID: "gpt-5.5",
    providerID: "openai",
    mode: "build",
    agent: "build",
    path: { cwd: "/", root: "/" },
    cost: 0,
    tokens: { input: 0, output: 0, reasoning: 0, cache: { read: 0, write: 0 } },
  } as Message,
  parts,
})

const text = (id: string, messageID: string, value: string) =>
  ({ id, sessionID: "session", messageID, type: "text", text: value } as Part)

describe("normalizeMessages", () => {
  test("merges assistant steps by parent while preserving part order", () => {
    const first = assistant("a1", "u1", 1100, 1300, [
      text("before", "a1", "Before tool"),
      {
        id: "tool",
        sessionID: "session",
        messageID: "a1",
        type: "tool",
        callID: "call",
        tool: "bash",
        state: { status: "completed", input: { command: "pwd" }, output: "/", title: "pwd", metadata: {}, time: { start: 1150, end: 1250 } },
      } as Part,
    ])
    const second = assistant("a2", "u1", 1400, 1800, [
      { id: "reasoning", sessionID: "session", messageID: "a2", type: "reasoning", text: "Checking", time: { start: 1400, end: 1500 } } as Part,
      text("after", "a2", "After tool"),
    ])

    const result = normalizeMessages([second, user("u1", 1000), first])

    expect(result).toHaveLength(2)
    expect(result[1]?.id).toBe("assistant-turn:u1")
    expect(result[1]?.blocks.map((block) => block.type)).toEqual(["text", "tool", "reasoning", "text"])
    expect(result[1]?.text).toBe("Before tool\n\nAfter tool")
    expect(result[1]?.durationMs).toBe(800)
    expect(result[1]?.agent).toBe("build")
    expect(result[1]?.model).toBe("gpt-5.5")
    expect(result[1]?.tokens?.total).toBe(0)
  })

  test("keeps the latest turn streaming while the session is busy", () => {
    const result = normalizeMessages([
      user("u1", 1000),
      assistant("a1", "u1", 1100, 1200, [text("answer", "a1", "Done")]),
    ], true)

    expect(result[1]?.streaming).toBe(true)
    expect(normalizeMessages([
      user("u1", 1000),
      assistant("a1", "u1", 1100, 1200, [text("answer", "a1", "Done")]),
    ])[1]?.streaming).toBe(false)
  })

  test("does not keep an idle session streaming when completion metadata is missing", () => {
    const incomplete = assistant("a1", "u1", 1100, 0, [text("answer", "a1", "Done")])
    delete (incomplete.info as any).time.completed

    expect(normalizeMessages([user("u1", 1000), incomplete])[1]?.streaming).toBe(false)
    expect(normalizeMessages([user("u1", 1000), incomplete], true)[1]?.streaming).toBe(true)
  })

  test("does not merge assistant messages from different user turns", () => {
    const result = normalizeMessages([
      user("u1", 1000),
      assistant("a1", "u1", 1100, 1200, [text("one", "a1", "One")]),
      user("u2", 2000),
      assistant("a2", "u2", 2100, 2200, [text("two", "a2", "Two")]),
    ])

    expect(result.map((message) => message.id)).toEqual(["u1", "assistant-turn:u1", "u2", "assistant-turn:u2"])
  })

  test("exposes a valid education preview job for progress polling", () => {
    const result = normalizeMessages([
      user("u1", 1000),
      assistant("a1", "u1", 1100, 0, [
        {
          id: "tool",
          sessionID: "session",
          messageID: "a1",
          type: "tool",
          callID: "call",
          tool: "education-content_education_wait_for_preview",
          state: {
            status: "running",
            input: { job_id: "job_123" },
            time: { start: 1150 },
          },
        } as Part,
      ]),
    ], true)

    expect(result[1]?.blocks[0]).toMatchObject({
      type: "tool",
      tool: { educationJobID: "job_123", status: "running" },
    })
  })

  test("does not expose invalid or unrelated tool inputs for progress polling", () => {
    const result = normalizeMessages([
      user("u1", 1000),
      assistant("a1", "u1", 1100, 0, [
        {
          id: "tool",
          sessionID: "session",
          messageID: "a1",
          type: "tool",
          callID: "call",
          tool: "education-content_education_wait_for_preview",
          state: { status: "running", input: { job_id: "../secret" }, time: { start: 1150 } },
        } as Part,
      ]),
    ], true)

    expect(result[1]?.blocks[0]).not.toHaveProperty("tool.educationJobID")
  })

  test("marks an education MCP request timeout as recoverable background work", () => {
    const result = normalizeMessages([
      user("u1", 1000),
      assistant("a1", "u1", 1100, 1200, [
        {
          id: "tool",
          sessionID: "session",
          messageID: "a1",
          type: "tool",
          callID: "call",
          tool: "education-content_education_wait_for_preview",
          state: {
            status: "error",
            input: { job_id: "job_123" },
            error: "MCP error -32001: Request timed out",
            time: { start: 1150, end: 1200 },
          },
        } as Part,
      ]),
    ])

    expect(result[1]?.blocks[0]).toMatchObject({
      type: "tool",
      tool: { educationJobID: "job_123", educationWaitTimedOut: true, status: "error" },
    })
  })

  test("renders a persisted workspace attachment once and hides its marker", () => {
    const raw = user("u1", 1000)
    const marker = serializeWorkspaceAttachment({
      name: "病例.txt",
      path: "attachments/session/file.txt",
      mime: "text/plain",
      size: 128,
      direct: true,
      filePartID: "file-part",
    })
    raw.parts = [
      text("marker", "u1", marker),
      {
        id: "file-part",
        sessionID: "session",
        messageID: "u1",
        type: "file",
        mime: "text/plain",
        filename: "病例.txt",
        url: "file:///workspace/attachments/session/file.txt",
      } as Part,
    ]

    const message = normalizeMessages([raw])[0]

    expect(message?.text).toBe("")
    expect(message?.blocks).toHaveLength(1)
    expect(message?.blocks[0]).toMatchObject({
      type: "file",
      file: {
        filename: "病例.txt",
        workspacePath: "attachments/session/file.txt",
        size: 128,
        direct: true,
      },
    })
  })
})

describe("incremental message events", () => {
  test("grows assistant text as part deltas arrive", () => {
    const info = assistant("a1", "u1", 1100, 0, []).info
    delete (info as any).time.completed
    let raw = upsertMessage([user("u1", 1000)], info)
    raw = upsertPart(raw, text("answer", "a1", ""))

    raw = appendPartDelta(raw, "a1", "answer", "text", "Hello")
    expect(normalizeMessages(raw, true)[1]?.text).toBe("Hello")

    raw = appendPartDelta(raw, "a1", "answer", "text", " world")
    expect(normalizeMessages(raw, true)[1]?.text).toBe("Hello world")
  })

  test("keeps streamed parts when an older HTTP snapshot arrives", () => {
    const snapshot = assistant("a1", "u1", 1100, 0, [text("answer", "a1", "Hello")])
    const current = assistant("a1", "u1", 1100, 0, [text("answer", "a1", "Hello world")])

    const merged = mergeMessageSnapshot([user("u1", 1000), snapshot], [user("u1", 1000), current])

    expect(normalizeMessages(merged)[1]?.text).toBe("Hello world")
  })

  test("accepts a newer HTTP part without shortening live text", () => {
    const snapshot = assistant("a1", "u1", 1100, 1200, [text("answer", "a1", "Hello world")])
    const current = assistant("a1", "u1", 1100, 0, [text("answer", "a1", "Hello")])
    delete (current.info as any).time.completed

    const merged = mergeMessageSnapshot([user("u1", 1000), snapshot], [user("u1", 1000), current])

    expect(normalizeMessages(merged)[1]?.text).toBe("Hello world")
    expect(normalizeMessages(merged)[1]?.streaming).toBe(false)
  })

  test("replaces a matching optimistic user message with the confirmed snapshot", () => {
    const optimistic = user("optimistic-1000", 1000)
    ;(optimistic.info as any).model.providerID = ""
    const confirmed = user("u1", 1050)

    const merged = mergeMessageSnapshot([confirmed], [optimistic])

    expect(merged.map((message) => message.info.id)).toEqual(["u1"])
  })
})

describe("filterRevertedMessages", () => {
  const history = [
    user("msg_001", 1000),
    assistant("msg_002", "msg_001", 1100, 1200, [text("one", "msg_002", "One")]),
    user("msg_003", 2000),
    assistant("msg_004", "msg_003", 2100, 2200, [text("two", "msg_004", "Two")]),
    user("msg_005", 3000),
    assistant("msg_006", "msg_005", 3100, 3200, [text("three", "msg_006", "Three")]),
  ]

  test("hides the boundary turn and everything after it", () => {
    expect(filterRevertedMessages(history, "msg_003").map((message) => message.info.id)).toEqual([
      "msg_001",
      "msg_002",
    ])
  })

  test("restores the full history when the boundary is cleared", () => {
    expect(filterRevertedMessages(history)).toEqual(history)
  })
})
