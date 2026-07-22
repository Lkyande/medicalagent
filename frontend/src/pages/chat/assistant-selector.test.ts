import { describe, expect, test } from "bun:test"
import type { PlatformAssistant } from "@/api/backend"
import { assistantOptions, resolveAssistant } from "./assistant-selector"

const clinical: PlatformAssistant = {
  id: "clinical-assistant",
  name: "诊疗助手",
  description: "临床辅助决策",
  version: "1.0.0",
  baseAgent: "build",
  systemPrompt: "临床系统提示",
  skills: ["clinical-decision-support", "disease-guidelines"],
  tools: ["murtagh-medical"],
}

describe("assistant selection", () => {
  test("keeps platform capabilities out of the base mode selector", () => {
    const result = assistantOptions([clinical])
    expect(result.map((item) => item.id)).toEqual(["build", "plan"])
  })

  test("includes a platform capability only when resolving its dedicated scene", () => {
    const result = assistantOptions([clinical], true)
    expect(result.map((item) => item.id)).toEqual(["build", "plan", "clinical-assistant"])
    expect(result[2]?.baseAgent).toBe("build")
    expect(result[2]?.systemPrompt).toBe("临床系统提示")
  })

  test("falls back to Build for an unknown profile", () => {
    expect(resolveAssistant([clinical], "unknown").id).toBe("build")
  })

  test("resolves a platform capability only when explicitly included", () => {
    expect(resolveAssistant([clinical], "clinical-assistant").id).toBe("build")
    expect(resolveAssistant([clinical], "clinical-assistant", true).id).toBe("clinical-assistant")
  })
})
