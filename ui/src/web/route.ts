// Which short is open, kept in the address so a reload, the back button or a shared link works:
//   #/video/why-foxes-hunt-20261126-091500.mp4
import { useSyncExternalStore } from "react";
import { videoName } from "../shared/schema";
import { toast } from "./toast";

export type Route = { video: string | null };
const HOME: Route = { video: null };

/** The address is typed by people and pasted from anywhere, so it is checked. */
export function parse(hash: string): Route | null {
  let parts: string[];
  try {
    parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  } catch {
    return null;
  }
  if (!parts.length) return HOME;
  const name = parts[0] === "video" && parts.length === 2 ? videoName.safeParse(parts[1]) : null;
  return name?.success ? { video: name.data } : null;
}

export const format = ({ video }: Route) => (video ? "#/video/" + encodeURIComponent(video) : "");

function read() {
  const route = parse(location.hash);
  if (route) return route;
  // A bad link is said out loud and removed, so the person is not left on a broken address.
  history.replaceState(null, "", location.pathname);
  toast.bad("That link is not valid. Showing your shorts instead.");
  return HOME;
}

let current = read();
const listeners = new Set<() => void>();
window.addEventListener("hashchange", () => {
  current = read();
  listeners.forEach((l) => l());
});

export const useRoute = () =>
  useSyncExternalStore(
    (l) => (listeners.add(l), () => listeners.delete(l)),
    () => current,
  );

export function go(route: Route) {
  const hash = format(route);
  if (hash) location.hash = hash;
  else {
    history.pushState(null, "", location.pathname);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  }
}
