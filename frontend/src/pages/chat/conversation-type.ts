export const CONVERSATION_TYPE_METADATA_KEY = "platformConversationType"
export const CLINICAL_CONVERSATION_TYPE = "clinical"
export const DISEASE_MANAGEMENT_CONVERSATION_TYPE = "disease-management"
export const EDUCATION_CONVERSATION_TYPE = "education"
export const GENERAL_CONVERSATION_TYPE = "general"
export const CLINICAL_SYSTEM_MARKER = "本会话启用了诊疗助手能力。"
export const DISEASE_MANAGEMENT_SYSTEM_MARKER = "本会话启用了疾病管理助手能力"
export const EDUCATION_SYSTEM_MARKER = "本会话启用了宣教助手能力。"

export type ConversationType =
  | typeof CLINICAL_CONVERSATION_TYPE
  | typeof DISEASE_MANAGEMENT_CONVERSATION_TYPE
  | typeof EDUCATION_CONVERSATION_TYPE
  | typeof GENERAL_CONVERSATION_TYPE

type SessionWithMetadata = {
  metadata?: Record<string, unknown>
}

type MessageWithSystem = {
  info: {
    role: string
    system?: unknown
  }
}

export function conversationTypeFromSession(session?: SessionWithMetadata): ConversationType | undefined {
  const value = session?.metadata?.[CONVERSATION_TYPE_METADATA_KEY]
  return value === CLINICAL_CONVERSATION_TYPE ||
    value === DISEASE_MANAGEMENT_CONVERSATION_TYPE ||
    value === EDUCATION_CONVERSATION_TYPE ||
    value === GENERAL_CONVERSATION_TYPE
    ? value
    : undefined
}

export function detectLegacyConversationType(
  messages: MessageWithSystem[],
  routeType: ConversationType,
): ConversationType {
  const diseaseManagement = messages.some(
    (message) =>
      message.info.role === "user" &&
      typeof message.info.system === "string" &&
      message.info.system.includes(DISEASE_MANAGEMENT_SYSTEM_MARKER),
  )
  if (diseaseManagement) return DISEASE_MANAGEMENT_CONVERSATION_TYPE
  const education = messages.some(
    (message) =>
      message.info.role === "user" &&
      typeof message.info.system === "string" &&
      message.info.system.includes(EDUCATION_SYSTEM_MARKER),
  )
  if (education) return EDUCATION_CONVERSATION_TYPE
  const clinical = messages.some(
    (message) =>
      message.info.role === "user" &&
      typeof message.info.system === "string" &&
      message.info.system.includes(CLINICAL_SYSTEM_MARKER),
  )
  if (clinical) return CLINICAL_CONVERSATION_TYPE
  if (messages.length > 0) return GENERAL_CONVERSATION_TYPE
  return routeType
}

export function conversationMetadata(
  type: ConversationType,
  existing?: Record<string, unknown>,
): Record<string, unknown> {
  return { ...existing, [CONVERSATION_TYPE_METADATA_KEY]: type }
}

export function conversationHref(id: string, type: ConversationType): string {
  const root = type === CLINICAL_CONVERSATION_TYPE
    ? "/clinical"
    : type === DISEASE_MANAGEMENT_CONVERSATION_TYPE
      ? "/disease-management"
    : type === EDUCATION_CONVERSATION_TYPE
      ? "/education"
      : "/chat"
  return `${root}/${id}`
}
