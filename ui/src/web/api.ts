// The one place the web app talks to the server.
import type { Progress } from "../render";
import type { DraftScript, ModelFields, RenderRequest, ScriptRequest, Settings } from "../shared/schema";

export type Choice = { value: string; label: string; description?: string | null };
export type SubtitlePreview = { font: string; primary: string; highlight: string; outline: string; uppercase: boolean; wordsPerScreen: number };
export type SubtitleStyleChoice = Choice & { preview?: SubtitlePreview };
/** usesPhoto: the engine looks at your models' photos; the others get their descriptions as words. */
export type Engine = Choice & { available: boolean; usesPhoto: boolean };
/** Every list the form offers. The Python tool owns them; nothing here is hardcoded in the page. */
export type Options = {
  presets: (Choice & { scenes: number })[];
  languages: (Choice & { defaultVoice: string; voices: string[] })[];
  voices: (Choice & { language: string })[];
  subtitleStyles: SubtitleStyleChoice[];
  subtitlePositions: Choice[];
  vfx: Choice[];
  motions: Choice[];
  stickActions: Choice[];
  stickCharacters?: Choice[];
  artStyles: (Choice & { category: string })[];
  transitions: Choice[];
  effects: Choice[];
  qualities: Choice[];
  music: string[];
  scriptEngines: Engine[];
  ttsEngines: Engine[];
  imageEngines: Engine[];
  videoEngines: Engine[];
  gemini: { configured: boolean; source: string | null; ending: string | null; voices: Choice[]; defaultVoice: string };
  fastmetal: { available: boolean; home: string; missing: string | null; fix: string | null; configured: string };
  myModels: { kinds: Choice[]; maxFeatured: number; maxName: number; maxDescription: number; describedByGemini: boolean };
  defaults: {
    language: string;
    voice: string;
    subtitleStyle: string;
    artStyle: string;
    transition: string;
    effects: string;
    quality: string;
    music: string;
    scriptEngine: string;
    ttsEngine: string;
    imageEngine: string;
    videoEngine: string;
  };
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
export type QueuedJob = { id: string; title: string; request: RenderRequest; queuedAt: number };
export type MyModel = { id: string; name: string; kind: string; description: string; created: number };
export type MyModels = { folder: string; models: MyModel[]; changed?: MyModel };
export type Video = { name: string; size: number; createdAt: number; title: string | null; caption: string | null; tags: string[]; hasProject?: boolean };
export type SavedProject = { script: DraftScript; project_dir: string; projectName: string | null; settings: Record<string, unknown> };

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
const model = (id: string) => "/models/" + encodeURIComponent(id);

export const api = {
  options: () => call<Options>("/options"),
  saveSettings: (settings: Settings) => call<Settings>("/settings", send("PUT", settings)),
  draftScript: (request: ScriptRequest) => call<{ script: DraftScript }>("/script", send("POST", request)),
  uploadFile: (file: File) => {
    const form = new FormData();
    form.set("file", file);
    return call<{ file: string; name: string }>("/uploads", { method: "POST", body: form });
  },
  voicePreviewUrl: (params: { engine: string; voice: string; language: string; rate?: string; voiceStyle?: string }) => {
    const qs = new URLSearchParams({ engine: params.engine, voice: params.voice, language: params.language });
    if (params.rate) qs.set("rate", params.rate);
    if (params.voiceStyle) qs.set("voiceStyle", params.voiceStyle);
    return `/api/voice-preview?${qs.toString()}`;
  },
  job: () => call<{ job: Job | null; queue: QueuedJob[] }>("/job"),
  start: (request: RenderRequest) => call<{ job: Job; queue: QueuedJob[]; queued: boolean }>("/job", send("POST", request)),
  cancel: () => call<{ job: Job; queue: QueuedJob[] }>("/job", send("DELETE")),
  removeQueued: (id: string) => call<{ job: Job | null; queue: QueuedJob[] }>(`/queue/${encodeURIComponent(id)}`, send("DELETE")),
  videos: () => call<{ videos: Video[] }>("/videos"),
  videoProject: (name: string) => call<SavedProject>(`${at(name)}/project`),
  deleteVideo: (name: string) => call<{ deleted: string }>(at(name), send("DELETE")),
  videoUrl: (name: string) => `/api${at(name)}/file`,
  posterUrl: (name: string) => `/api${at(name)}/poster`,
  downloadUrl: (name: string) => `/api${at(name)}/file?download`,
  myModels: () => call<MyModels>("/models"),
  addModel: (photo: File, fields: ModelFields) => {
    const form = new FormData();
    form.set("photo", photo);
    for (const [name, value] of Object.entries(fields)) form.set(name, value);
    return call<MyModels>("/models", { method: "POST", body: form });
  },
  editModel: (id: string, fields: ModelFields) => call<MyModels>(model(id), send("PATCH", fields)),
  deleteModel: (id: string) => call<MyModels>(model(id), send("DELETE")),
  modelPhotoUrl: (m: MyModel) => `/api${model(m.id)}/photo?v=${m.created}`,
};
