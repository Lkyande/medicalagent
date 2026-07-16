export interface SessionItem {
  id: string
  title: string
  time?: { created?: number; updated?: number; archived?: number }
}

export type TimeGroup = "today" | "yesterday" | "earlier"

export interface SessionGroup<T extends SessionItem = SessionItem> {
  label: string
  items: T[]
}

const DAY_MS = 24 * 60 * 60 * 1000

function startOfDay(ts: number): number {
  const d = new Date(ts)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

export function getGroupTimestamp(item: SessionItem): number {
  return item.time?.updated ?? item.time?.created ?? 0
}

export function classifyByTime(ts: number, now: number = Date.now()): TimeGroup {
  const todayStart = startOfDay(now)
  const yesterdayStart = todayStart - DAY_MS
  if (ts >= todayStart) return "today"
  if (ts >= yesterdayStart) return "yesterday"
  return "earlier"
}

export function groupByTime<T extends SessionItem>(items: T[], now: number = Date.now()): SessionGroup<T>[] {
  const buckets: Record<TimeGroup, T[]> = {
    today: [],
    yesterday: [],
    earlier: [],
  }

  for (const item of items) {
    const ts = getGroupTimestamp(item)
    if (!ts) {
      buckets.earlier.push(item)
      continue
    }
    const group = classifyByTime(ts, now)
    buckets[group].push(item)
  }

  const sortDesc = (a: T, b: T) => getGroupTimestamp(b) - getGroupTimestamp(a)

  const labels: Record<TimeGroup, string> = {
    today: "今天",
    yesterday: "昨天",
    earlier: "更早",
  }

  const result: SessionGroup<T>[] = []
  for (const key of ["today", "yesterday", "earlier"] as TimeGroup[]) {
    if (buckets[key].length > 0) {
      result.push({
        label: labels[key],
        items: buckets[key].sort(sortDesc),
      })
    }
  }
  return result
}

export function formatRelativeTime(ts: number): string {
  if (!ts) return ""
  const now = Date.now()
  const diff = now - ts
  if (diff < 60_000) return "刚刚"
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)}分钟前`
  if (diff < DAY_MS) return `${Math.floor(diff / 3600_000)}小时前`
  const days = Math.floor(diff / DAY_MS)
  if (days < 7) return `${days}天前`
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()}`
}
