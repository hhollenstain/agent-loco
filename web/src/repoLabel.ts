export function repoLabel(remote: string, name: string): string {
  let text = String(remote || "").trim();
  if (text.endsWith(".git")) text = text.slice(0, -4);
  const ssh = text.match(/^git@[^:]+:(.+)$/);
  if (ssh) return ssh[1];
  try {
    const url = new URL(text);
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length >= 2) return `${parts[0]}/${parts[1]}`;
    if (parts.length === 1) return parts[0];
  } catch {
    /* a remote that is not a URL stays as written */
  }
  return text || name;
}
