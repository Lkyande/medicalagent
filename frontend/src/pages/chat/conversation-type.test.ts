import { describe, expect, test } from "bun:test"
import {
  CLINICAL_CONVERSATION_TYPE,
  EDUCATION_CONVERSATION_TYPE,
  GENERAL_CONVERSATION_TYPE,
  conversationHref,
  conversationMetadata,
  conversationTypeFromSession,
  detectLegacyConversationType,
} from "./conversation-type"

describe("conversation type", () => {
  test("reads persisted clinical and general types", () => {
    expect(conversationTypeFromSession({ metadata: { platformConversationType: "clinical" } })).toBe(
      CLINICAL_CONVERSATION_TYPE,
    )
    expect(conversationTypeFromSession({ metadata: { platformConversationType: "general" } })).toBe(
      GENERAL_CONVERSATION_TYPE,
    )
    expect(conversationTypeFromSession({ metadata: { platformConversationType: "unknown" } })).toBeUndefined()
  })

  test("detects a legacy clinical conversation from its system prompt", () => {
    const result = detectLegacyConversationType(
      [{ info: { role: "user", system: "本会话启用了诊疗助手能力。请执行临床规则。" } }],
      GENERAL_CONVERSATION_TYPE,
    )

    expect(result).toBe(CLINICAL_CONVERSATION_TYPE)
  })

  test("keeps non-clinical legacy conversations general", () => {
    expect(detectLegacyConversationType([{ info: { role: "user" } }], CLINICAL_CONVERSATION_TYPE)).toBe(GENERAL_CONVERSATION_TYPE)
  })

  test("uses the entry route only for an empty legacy conversation", () => {
    expect(detectLegacyConversationType([], CLINICAL_CONVERSATION_TYPE)).toBe(CLINICAL_CONVERSATION_TYPE)
    expect(detectLegacyConversationType([], EDUCATION_CONVERSATION_TYPE)).toBe(EDUCATION_CONVERSATION_TYPE)
    expect(detectLegacyConversationType([], GENERAL_CONVERSATION_TYPE)).toBe(GENERAL_CONVERSATION_TYPE)
  })

  test("builds canonical links and preserves unrelated metadata", () => {
    expect(conversationHref("ses_1", CLINICAL_CONVERSATION_TYPE)).toBe("/clinical/ses_1")
    expect(conversationHref("ses_education", EDUCATION_CONVERSATION_TYPE)).toBe("/education/ses_education")
    expect(conversationHref("ses_2", GENERAL_CONVERSATION_TYPE)).toBe("/chat/ses_2")
    expect(conversationMetadata(CLINICAL_CONVERSATION_TYPE, { retained: true })).toEqual({
      retained: true,
      platformConversationType: "clinical",
    })
  })
})
