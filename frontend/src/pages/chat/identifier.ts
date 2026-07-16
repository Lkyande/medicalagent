const RANDOM_LENGTH = 14
const BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

let lastTimestamp = 0
let counter = 0

/** Generate IDs with the same monotonic encoding used by OpenCode. */
export function createChatIdentifier(prefix: "msg" | "prt", timestamp = Date.now()) {
  if (timestamp !== lastTimestamp) {
    lastTimestamp = timestamp
    counter = 0
  }
  counter++

  const value = BigInt(timestamp) * 0x1000n + BigInt(counter)
  const time = Array.from({ length: 6 }, (_, index) =>
    Number((value >> BigInt(40 - 8 * index)) & 0xffn)
      .toString(16)
      .padStart(2, "0"),
  ).join("")
  const bytes = crypto.getRandomValues(new Uint8Array(RANDOM_LENGTH))
  const random = Array.from(bytes, (byte) => BASE62[byte % BASE62.length]).join("")
  return `${prefix}_${time}${random}`
}
