import { describe, expect, test } from "bun:test"
import { parseWorkspaceAttachment, serializeWorkspaceAttachment } from "./workspace-attachment"

describe("workspace attachment marker", () => {
  test("round trips a valid attachment", () => {
    const attachment = {
      name: "病例.txt",
      path: "attachments/ses_1/file.txt",
      mime: "text/plain",
      size: 42,
      direct: true,
      filePartID: "prt_1",
      publicUrl: "https://example.com/image.png",
      drawingSessionId: undefined,
    }

    expect(parseWorkspaceAttachment(serializeWorkspaceAttachment(attachment))).toEqual(attachment)
  })

  test("round trips a drawing session id", () => {
    const attachment = {
      name: "drawing.pdf",
      path: "attachments/ses_1/drawing.pdf",
      mime: "application/pdf",
      size: 42,
      direct: true,
      drawingSessionId: "drawing_0123456789abcdef0123456789abcdef",
    }

    expect(parseWorkspaceAttachment(serializeWorkspaceAttachment(attachment))).toEqual(attachment)
  })

  test("rejects paths outside the attachment directory", () => {
    const marker = '<workspace_attachment>{"name":"x","path":"../x","mime":"text/plain","size":1,"direct":true}</workspace_attachment>'
    expect(parseWorkspaceAttachment(marker)).toBeUndefined()
  })
})
