export function resolveBrowserUrl(configured: string): string {
  if (!configured.startsWith("http://") && !configured.startsWith("https://")) return configured

  const url = new URL(configured)
  const configuredLocally = url.hostname === "localhost" || url.hostname === "127.0.0.1"
  const pageIsRemote = window.location.hostname !== "localhost" && window.location.hostname !== "127.0.0.1"
  if (configuredLocally && pageIsRemote) url.hostname = window.location.hostname
  return url.toString().replace(/\/$/, "")
}
