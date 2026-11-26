/**
 * Who may call the API. The request must be addressed to this computer by a name we know: its own
 * (localhost) or one of extraHosts, such as its tailnet name. That blocks DNS rebinding.
 * A request made by another website carries that site's Origin and is refused.
 */
export function allowed(req: Request, extraHosts: readonly string[] = []) {
  const url = new URL(req.url);
  if (!["localhost", "127.0.0.1", ...extraHosts].includes(url.hostname)) return false;
  const origin = req.headers.get("origin");
  if (!origin || origin === url.origin) return true;
  // Behind "tailscale serve" the page is https on the tailnet name while we are reached as plain http.
  try {
    return extraHosts.includes(new URL(origin).hostname);
  } catch {
    return false;
  }
}
