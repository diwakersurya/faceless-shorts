// The one place the web app talks to the server.
import type { Progress } from "../render";
import type { RenderRequest, Settings } from "../shared/schema";

export type Choice = { value: string; label: string; description?: string | null };
export type Engine = Choice & { available: boolean };
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
  /** available: false when the engine needs something installed, running or saved; its description then says what. */
  scriptEngines: Engine[];
  ttsEngines: Engine[];
  imageEngines: Engine[];
  videoEngines: Engine[];
  /** Whether a Gemini API key is there, where it comes from ("settings" or the name of an environment variable) and its last 4 characters. Never the key. voices: the voices Gemini speaks in (its own, the same for every language). */
  gemini: { configured: boolean; source: string | null; ending: string | null; voices: Choice[]; defaultVoice: string };
  /** The local video model: whether it is ready, where it was looked for, what was not there and the command that fixes it. */
  fastmetal: { available: boolean; home: string; missing: string | null; fix: string | null; configured: string };
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
  posterUrl: (name: string) => `/api${at(name)}/poster`,
  downloadUrl: (name: string) => `/api${at(name)}/file?download`,
};
