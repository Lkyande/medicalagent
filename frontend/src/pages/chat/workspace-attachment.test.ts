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
    }

    expect(parseWorkspaceAttachment(serializeWorkspaceAttachment(attachment))).toEqual(attachment)
  })

  test("rejects paths outside the attachment directory", () => {
    const marker = '<workspace_attachment>{"name":"x","path":"../x","mime":"text/plain","size":1,"direct":true}</workspace_attachment>'
    expect(parseWorkspaceAttachment(marker)).toBeUndefined()
  })
})
