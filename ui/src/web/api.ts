// The one place the web app talks to the server.
import type { Progress } from "../render";
import type { RenderRequest, Settings } from "../shared/schema";

export type Choice = { value: string; label: string; description?: string };
/** Every list the form offers. The Python tool owns them; nothing here is hardcoded in the page. */
export type Options = {
  presets: (Choice & { scenes: number })[];
  languages: (Choice & { defaultVoice: string; voices: string[] })[];
  voices: (Choice & { language: string })[];
  subtitleStyles: Choice[];
  artStyles: (Choice & { category: string })[];
  transitions: Choice[];
  effects: Choice[];
  qualities: Choice[];
  music: string[];
  imageEngines: Choice[];
  videoEngines: Choice[];
  /** The local video model: whether it was found, where it was looked for, and what was not there. */
  fastmetal: { available: boolean; home: string; missing: string | null };
  defaults: Omit<RenderRequest, "source" | "topic" | "preset" | "progressBar">;
  settings: Settings;
};
export type Job = {
  id: string;
  state: "running" | "done" | "failed" | "cancelled";
  title: string;
  request: RenderRequest;
  startedAt: number;
  finishedAt: number | null;
  progress: Progress;
  log: string[];
  video: string;
  error: string | null;
};
export type Video = { name: string; size: number; createdAt: number; title: string | null; caption: string | null; tags: string[] };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch("/api" + path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body as T;
}

const send = (method: string, data?: unknown): RequestInit => ({
  method,
  ...(data === undefined ? {} : { body: JSON.stringify(data), headers: { "content-type": "application/json" } }),
});
const at = (name: string) => "/videos/" + encodeURIComponent(name);

export const api = {
  options: () => call<Options>("/options"),
  saveSettings: (settings: Settings) => call<Settings>("/settings", send("PUT", settings)),
  job: () => call<{ job: Job | null }>("/job"),
  start: (request: RenderRequest) => call<{ job: Job }>("/job", send("POST", request)),
  cancel: () => call<{ job: Job }>("/job", send("DELETE")),
  videos: () => call<{ videos: Video[] }>("/videos"),
  deleteVideo: (name: string) => call<{ deleted: string }>(at(name), send("DELETE")),
  videoUrl: (name: string) => `/api${at(name)}/file`,
  downloadUrl: (name: string) => `/api${at(name)}/file?download`,
};
