#!/usr/bin/env bun
// The web app's server: serves the page, manages a FIFO render queue, draft scripts, uploads, and voice previews.
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readdir, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, extname, join, resolve } from "node:path";
import { allowed } from "./access";
import { type Progress, START, buildArgs, failureReason, outputName, parseLine } from "./render";
import {
  type DraftScript,
  type RenderRequest,
  type ScriptRequest,
  anyModelFields,
  modelId,
  projectName as projectNameSchema,
  renderRequest,
  scriptRequestSchema,
  settings as settingsSchema,
  uploadName,
  videoName,
} from "./shared/schema";
import app from "./web/index.html";

const UI = resolve(import.meta.dir, "..");
const REPO = resolve(UI, ".."); // the Python tool runs from the repository root
const OUTPUT = join(REPO, "output");
const UPLOADS = join(OUTPUT, ".uploads");
const PROJECTS = join(OUTPUT, ".projects");
const PREVIEWS = join(OUTPUT, ".previews");
const CONFIG = join(UI, "config.json");

const defaults = {
  port: 4780,
  host: "127.0.0.1", // answers only on this computer
  python: "python3", // the Python that has faceless_shorts' requirements installed
  allowedHosts: [] as string[],
};
const saved = (await Bun.file(CONFIG).exists()) ? await Bun.file(CONFIG).json() : {};
const cfg: typeof defaults = { ...defaults, ...saved };

const json = (body: unknown, status = 200) => Response.json(body, { status });
const fail = (status: number, message: string) => Object.assign(new Error(message), { status });
const lastLine = (text: string) => text.trim().split("\n").at(-1) ?? "";

async function tailnet(): Promise<{ name: string; bin: string } | { problem: string }> {
  const inApp = "/Applications/Tailscale.app/Contents/MacOS/Tailscale";
  const bin = Bun.which("tailscale") ? "tailscale" : existsSync(inApp) ? inApp : null;
  if (!bin) return { problem: "Tailscale is not installed on this computer. Install it from https://tailscale.com/download and sign in, then run this again." };
  const res = await Bun.$`${bin} status --json`.quiet().nothrow();
  try {
    const status = JSON.parse(res.stdout.toString());
    const name = (status.Self?.DNSName as string | undefined)?.replace(/\.$/, "");
    if (status.BackendState !== "Running") return { problem: "Tailscale is switched off on this computer. Switch it on (and sign in if it asks), then run this again." };
    if (name) return { name, bin };
  } catch {}
  return { problem: "Tailscale is not signed in on this computer. Open Tailscale and sign in, then run this again." };
}

let phone = "";
if (process.argv.includes("--tailnet")) {
  const ts = await tailnet();
  if ("problem" in ts) {
    console.error(ts.problem);
    process.exit(1);
  }
  cfg.allowedHosts = [...cfg.allowedHosts, ts.name];
  phone = `\nFrom a phone on your tailnet: https://${ts.name}/\n  Turn the link on (once):  ${ts.bin} serve --bg ${cfg.port}\n  Turn it off:              ${ts.bin} serve reset`;
}

const pythonEnv = () => ({ ...process.env, PYTHONUNBUFFERED: "1" });

// ---------------------------------------------------------------- choices
let choices: { at: number; value: Record<string, unknown> & { fastmetal: { configured: string } } } | null = null;
async function options() {
  if (choices && Date.now() - choices.at < 30_000) return choices.value;
  const started = `${cfg.python} -m faceless_shorts options`;
  let out: string;
  try {
    const proc = Bun.spawn([cfg.python, "-m", "faceless_shorts", "options"], { cwd: REPO, env: pythonEnv(), stdout: "pipe", stderr: "pipe" });
    const [stdout, stderr, code] = await Promise.all([new Response(proc.stdout).text(), new Response(proc.stderr).text(), proc.exited]);
    if (code !== 0) throw new Error(failureReason(stderr.split("\n")));
    out = stdout;
  } catch (e) {
    throw fail(503, `The Python tool did not start (${started}): ${e instanceof Error ? e.message : e}. Install its requirements with: pip install -r requirements.txt`);
  }
  choices = { at: Date.now(), value: JSON.parse(out) };
  return choices.value;
}

// ------------------------------------------------------------------ render + queue
type Job = {
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
type QueuedJob = {
  id: string;
  title: string;
  request: RenderRequest;
  queuedAt: number;
};

let job: Job | null = null;
let running: Bun.Subprocess | null = null;
const queue: QueuedJob[] = [];

const jobTitle = (req: RenderRequest) =>
  req.source === "preset" ? req.preset.replace(/_/g, " ") : req.source === "custom" ? (req.script?.title || "Custom short") : req.topic;

async function pump(stream: ReadableStream<Uint8Array>, onLine: (line: string) => void) {
  const decoder = new TextDecoder();
  let rest = "";
  for await (const chunk of stream) {
    const lines = (rest + decoder.decode(chunk, { stream: true })).split(/\r?\n|\r/);
    rest = lines.pop()!;
    lines.forEach(onLine);
  }
  if (rest) onLine(rest);
}

function resolveUpload(file: string | null | undefined): string | undefined {
  if (!file) return undefined;
  if (!uploadName.safeParse(file).success) return undefined;
  const full = join(UPLOADS, file);
  return existsSync(full) ? full : undefined;
}

function hydrateScriptMedia(script: DraftScript): DraftScript {
  return {
    ...script,
    scenes: script.scenes.map((sc) => {
      if (!sc.media_path) return sc;
      // Uploaded keys stored under output/.uploads/ are resolved to real paths; existing absolute paths pass through.
      const resolved = resolveUpload(sc.media_path);
      return resolved ? { ...sc, media_path: resolved } : sc;
    }),
  };
}

async function launch(request: RenderRequest) {
  await mkdir(OUTPUT, { recursive: true });
  await mkdir(PROJECTS, { recursive: true });
  const video = outputName(request);
  const projKey = request.projectName && projectNameSchema.safeParse(request.projectName).success
    ? request.projectName
    : video.replace(/\.mp4$/, "");
  const projectDir = join(PROJECTS, projKey);
  await mkdir(projectDir, { recursive: true });

  let scriptJsonPath: string | undefined;
  if (request.script) {
    scriptJsonPath = join(projectDir, "draft_script.json");
    await Bun.write(scriptJsonPath, JSON.stringify(hydrateScriptMedia(request.script), null, 2));
  }

  const musicFilePath = resolveUpload(request.customMusicFile);
  const voiceoverPath = resolveUpload(request.customVoiceoverFile);

  const mine: Job = {
    id: crypto.randomUUID(),
    state: "running",
    title: jobTitle(request),
    request,
    startedAt: Date.now(),
    finishedAt: null,
    progress: START,
    log: [],
    video,
    error: null,
  };
  const args = buildArgs(request, join("output", video), { scriptJsonPath, projectDir, musicFilePath, voiceoverPath });
  const proc = Bun.spawn([cfg.python, ...args], { cwd: REPO, env: pythonEnv(), stdout: "pipe", stderr: "pipe" });
  job = mine;
  running = proc;
  const onLine = (raw: string) => {
    const line = raw.replace(/\x1b\[[0-9;]*m/g, "").trimEnd();
    if (!line.trim()) return;
    mine.log.push(line);
    if (mine.log.length > 400) mine.log.shift();
    mine.progress = parseLine(line, mine.progress);
  };
  void Promise.all([pump(proc.stdout, onLine), pump(proc.stderr, onLine), proc.exited]).then(async ([, , code]) => {
    if (running === proc) running = null;
    mine.finishedAt = Date.now();
    if (mine.state !== "cancelled") {
      if (code === 0 && existsSync(join(OUTPUT, video))) mine.state = "done";
      else {
        mine.state = "failed";
        mine.error = failureReason(mine.log);
      }
    }
    // Start next queued render automatically.
    const next = queue.shift();
    if (next) void launch(next.request);
  });
  return mine;
}

async function startOrQueue(request: RenderRequest) {
  if (job?.state === "running") {
    const item: QueuedJob = { id: crypto.randomUUID(), title: jobTitle(request), request, queuedAt: Date.now() };
    queue.push(item);
    return { job, queue, queued: true };
  }
  const active = await launch(request);
  return { job: active, queue, queued: false };
}

async function stopTree(pid: number) {
  const family = [pid];
  for (const parent of family) {
    const kids = await Bun.$`pgrep -P ${parent}`.nothrow().quiet().text();
    family.push(...kids.split("\n").filter(Boolean).map(Number));
  }
  for (const member of family) {
    try {
      process.kill(member, "SIGTERM");
    } catch {}
  }
}

async function cancel() {
  if (job?.state !== "running" || !running) throw fail(409, "Nothing is being made right now.");
  job.state = "cancelled";
  await stopTree(running.pid);
  return job;
}

for (const signal of ["SIGINT", "SIGTERM"] as const)
  process.on(signal, async () => {
    if (running) await stopTree(running.pid);
    process.exit(0);
  });

// ------------------------------------------------------------------ draft script + voice preview + uploads
async function draftScript(req: ScriptRequest) {
  const args = [
    "-m",
    "faceless_shorts",
    "script",
    req.source === "preset" ? `--niche=${req.preset}` : `--topic=${req.topic}`,
    `--language=${req.language}`,
    `--script-engine=${req.scriptEngine}`,
    ...(req.sceneCount ? [`--scenes=${req.sceneCount}`] : []),
    ...(req.tone ? [`--tone=${req.tone}`] : []),
    ...(req.stickman ? ["--stickman"] : []),
    "--json",
  ];
  const proc = Bun.spawn([cfg.python, ...args], { cwd: REPO, env: pythonEnv(), stdout: "pipe", stderr: "pipe" });
  const [stdout, stderr, code] = await Promise.all([new Response(proc.stdout).text(), new Response(proc.stderr).text(), proc.exited]);
  if (code !== 0) throw fail(400, lastLine(stderr) || "Could not draft the script.");
  return JSON.parse(stdout) as DraftScript;
}

async function voicePreview(url: URL) {
  const engine = url.searchParams.get("engine") || "edge";
  const voice = url.searchParams.get("voice") || "christopher";
  const language = url.searchParams.get("language") || "en";
  const rate = url.searchParams.get("rate") || "+0%";
  const voiceStyle = url.searchParams.get("voiceStyle") || "";
  if (!/^[\w-]{1,40}$/.test(engine) || !/^[\w-]{1,40}$/.test(voice) || !/^[\w-]{1,12}$/.test(language)) {
    throw fail(400, "Invalid voice preview parameters.");
  }
  await mkdir(PREVIEWS, { recursive: true });
  const ext = engine === "gemini" ? ".wav" : ".mp3";
  const hash = createHash("sha1").update([engine, voice, language, rate, voiceStyle].join("|")).digest("hex").slice(0, 16);
  const outPath = join(PREVIEWS, `${engine}-${voice}-${hash}${ext}`);
  if (!existsSync(outPath)) {
    const args = [
      "-m",
      "faceless_shorts",
      "voice-preview",
      `--tts-engine=${engine}`,
      `--voice=${voice}`,
      `--language=${language}`,
      `--rate=${rate}`,
      ...(voiceStyle ? [`--voice-style=${voiceStyle}`] : []),
      `--output=${outPath}`,
    ];
    const proc = Bun.spawn([cfg.python, ...args], { cwd: REPO, env: pythonEnv(), stdout: "pipe", stderr: "pipe" });
    const [stderr, code] = await Promise.all([new Response(proc.stderr).text(), proc.exited]);
    if (code !== 0 || !existsSync(outPath)) throw fail(400, lastLine(stderr) || "Could not generate voice preview.");
  }
  return new Response(Bun.file(outPath), { headers: { "cache-control": "private, max-age=86400" } });
}

const UPLOAD_EXTS: Record<string, string> = {
  "audio/mpeg": ".mp3",
  "audio/mp3": ".mp3",
  "audio/wav": ".wav",
  "audio/x-wav": ".wav",
  "audio/mp4": ".m4a",
  "audio/x-m4a": ".m4a",
  "audio/ogg": ".ogg",
  "image/jpeg": ".jpg",
  "image/png": ".png",
  "image/webp": ".webp",
  "video/mp4": ".mp4",
  "video/quicktime": ".mov",
  "video/webm": ".webm",
};
const ALLOWED_UPLOAD_EXT_SET = new Set(Object.values(UPLOAD_EXTS));
const MAX_UPLOAD_BYTES = 100_000_000;

async function handleUpload(req: Req) {
  const form = await req.formData().catch(() => null);
  const file = form?.get("file");
  if (!(file instanceof File) || file.size === 0) throw fail(400, "Choose a file to upload.");
  if (file.size > MAX_UPLOAD_BYTES) throw fail(400, "File is larger than 100 MB.");
  const extFromType = UPLOAD_EXTS[file.type];
  const extFromName = extname(file.name || "").toLowerCase();
  const ext = extFromType || (ALLOWED_UPLOAD_EXT_SET.has(extFromName) ? extFromName : "");
  if (!ext) throw fail(400, "Unsupported file type. Use MP3/WAV/M4A for audio, JPG/PNG/WebP for images, or MP4/MOV/WebM for video clips.");
  await mkdir(UPLOADS, { recursive: true });
  const stored = `${crypto.randomUUID()}${ext}`;
  await Bun.write(join(UPLOADS, stored), file);
  return { file: stored, name: file.name || stored };
}

// ------------------------------------------------------------------ videos
async function videos() {
  const names = existsSync(OUTPUT) ? (await readdir(OUTPUT)).filter((n) => videoName.safeParse(n).success) : [];
  const rows = await Promise.all(
    names.map(async (name) => {
      const info = await stat(join(OUTPUT, name));
      const meta = await Bun.file(join(OUTPUT, name.replace(/\.mp4$/, "_metadata.json"))).json().catch(() => null);
      const hasProject = existsSync(join(OUTPUT, name.replace(/\.mp4$/, "_project.json")));
      return {
        name,
        size: info.size,
        createdAt: Math.round(info.mtimeMs / 1000),
        title: (meta?.youtube?.title as string | undefined) ?? null,
        caption: (meta?.tiktok?.caption as string | undefined) ?? null,
        tags: (meta?.youtube?.tags as string[] | undefined) ?? [],
        hasProject,
      };
    }),
  );
  const unfinished = job?.state === "running" ? job.video : null;
  return rows.filter((v) => v.size > 0 && v.name !== unfinished).sort((a, b) => b.createdAt - a.createdAt);
}

async function videoProject(name: string | undefined) {
  const path = videoPath(name);
  const projPath = path.replace(/\.mp4$/, "_project.json");
  if (!existsSync(projPath)) throw fail(404, "No editable project was saved for this short.");
  const data = await Bun.file(projPath).json();
  const projDir = typeof data.project_dir === "string" ? data.project_dir : "";
  const projName = projDir && projDir.startsWith(PROJECTS) ? basename(projDir) : null;
  return { ...data, projectName: projName };
}

async function poster(name: string | undefined) {
  const video = videoPath(name);
  const image = join(OUTPUT, ".posters", name!.replace(/\.mp4$/, ".jpg"));
  if (!existsSync(image)) {
    await mkdir(dirname(image), { recursive: true });
    const made = Bun.spawn(["ffmpeg", "-y", "-v", "error", "-ss", "0.5", "-i", video, "-frames:v", "1", "-vf", "scale=360:-2", image], { stdout: "ignore", stderr: "ignore" });
    if ((await made.exited) !== 0 || !existsSync(image)) throw fail(500, "Could not make a picture of this short.");
  }
  return image;
}

function videoPath(name: string | undefined) {
  const parsed = videoName.safeParse(name);
  if (!parsed.success) throw fail(400, "That is not a video name.");
  const path = join(OUTPUT, parsed.data);
  if (!existsSync(path)) throw fail(404, "That short is no longer there.");
  return path;
}

// ------------------------------------------------------------- your models
type MyModel = { id: string; name: string; kind: string; description: string; created: number };
let modelsFolder: string | null = null;
const PHOTO_TYPES: Record<string, string> = { "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp" };
const MAX_PHOTO = 20_000_000;

async function myModels(args: string[] = ["list"]) {
  const proc = Bun.spawn([cfg.python, "-m", "faceless_shorts", "models", ...args, "--json"], { cwd: REPO, env: pythonEnv(), stdout: "pipe", stderr: "pipe" });
  const [stdout, stderr, code] = await Promise.all([new Response(proc.stdout).text(), new Response(proc.stderr).text(), proc.exited]);
  if (code !== 0) throw fail(400, lastLine(stderr) || "The model could not be saved.");
  const answer = JSON.parse(stdout) as { folder: string; models: MyModel[]; changed?: MyModel };
  modelsFolder = answer.folder;
  return answer;
}

const fieldArgs = (f: { name: string; kind: string; description: string }) => [`--name=${f.name}`, `--kind=${f.kind}`, `--description=${f.description}`];

async function addModel(req: Req) {
  const form = await req.formData().catch(() => null);
  const photo = form?.get("photo");
  if (!(photo instanceof File) || photo.size === 0) throw fail(400, "Choose a photo.");
  const ext = PHOTO_TYPES[photo.type];
  if (!ext) throw fail(400, "Use a JPEG, PNG or WebP photo. (An iPhone HEIC photo: share it as JPEG, or take a screenshot of it.)");
  if (photo.size > MAX_PHOTO) throw fail(400, `The photo is larger than ${MAX_PHOTO / 1e6} MB. Use a smaller one.`);
  const fields = anyModelFields.safeParse({ name: form!.get("name"), kind: form!.get("kind"), description: form!.get("description") ?? "" });
  if (!fields.success) throw fail(400, fields.error.issues[0]?.message ?? "bad request");
  const upload = join(tmpdir(), `faceless-model-${crypto.randomUUID()}${ext}`);
  await Bun.write(upload, photo);
  try {
    return await myModels(["add", ...fieldArgs(fields.data), `--image=${upload}`]);
  } finally {
    await rm(upload, { force: true });
  }
}

function modelParam(req: Req) {
  const id = modelId.safeParse(req.params.id);
  if (!id.success) throw fail(400, "That is not a model.");
  return id.data;
}

async function modelPhoto(id: string) {
  if (!modelsFolder) await myModels();
  const path = join(modelsFolder!, id, "photo.jpg");
  if (!existsSync(path)) throw fail(404, "That model is no longer there.");
  return path;
}

// ------------------------------------------------------------------ server
type Req = Bun.BunRequest<any>;
const api = (handler: (req: Req) => Response | Promise<Response>) => async (req: Req) => {
  if (!allowed(req, cfg.allowedHosts)) return json({ error: "forbidden" }, 403);
  try {
    return await handler(req);
  } catch (e) {
    return json({ error: e instanceof Error ? e.message : String(e) }, (e as { status?: number }).status ?? 500);
  }
};
const body = async <T>(req: Req, schema: { safeParse: (v: unknown) => { success: true; data: T } | { success: false; error: { issues: { message: string }[] } } }) => {
  const parsed = schema.safeParse(await req.json().catch(() => null));
  if (!parsed.success) throw fail(400, parsed.error.issues[0]?.message ?? "bad request");
  return parsed.data;
};

if (await fetch(`http://${cfg.host}:${cfg.port}/api/job`, { signal: AbortSignal.timeout(1000) }).then(() => true, () => false)) {
  console.error(`Something already answers on port ${cfg.port}, probably another copy of this app. Stop it first, or set another "port" in ui/config.json.`);
  process.exit(1);
}

const server = Bun.serve({
  hostname: cfg.host,
  port: cfg.port,
  development: process.argv.includes("--dev"),
  routes: {
    "/": app,
    "/icon.png": () => new Response(Bun.file(join(import.meta.dir, "web/icon.png"))),
    "/api/options": {
      GET: api(async () => {
        const all = await options();
        return json({ ...all, settings: { fastmetalHome: all.fastmetal.configured } });
      }),
    },
    "/api/settings": {
      PUT: api(async (req) => {
        const { fastmetalHome, geminiKey } = await body(req, settingsSchema);
        const keyArgs = geminiKey === undefined ? [] : ["--gemini-key=-"];
        const proc = Bun.spawn([cfg.python, "-m", "faceless_shorts", "config", `--fastmetal-home=${fastmetalHome}`, ...keyArgs], {
          cwd: REPO,
          env: pythonEnv(),
          stdin: geminiKey === undefined ? "ignore" : new Blob([geminiKey + "\n"]),
          stdout: "ignore",
          stderr: "pipe",
        });
        const [stderr, code] = await Promise.all([new Response(proc.stderr).text(), proc.exited]);
        if (code !== 0) throw fail(400, lastLine(stderr) || "The settings could not be saved.");
        choices = null;
        return json({ fastmetalHome });
      }),
    },
    "/api/script": {
      POST: api(async (req) => json({ script: await draftScript(await body(req, scriptRequestSchema)) })),
    },
    "/api/voice-preview": {
      GET: api((req) => voicePreview(new URL(req.url))),
    },
    "/api/uploads": {
      POST: api(async (req) => json(await handleUpload(req))),
    },
    "/api/job": {
      GET: api(() => json({ job, queue })),
      POST: api(async (req) => json(await startOrQueue(await body(req, renderRequest)))),
      DELETE: api(async () => json({ job: await cancel(), queue })),
    },
    "/api/queue/:id": {
      DELETE: api((req) => {
        const idx = queue.findIndex((q) => q.id === req.params.id);
        if (idx === -1) throw fail(404, "That queued item is no longer in the queue.");
        queue.splice(idx, 1);
        return json({ job, queue });
      }),
    },
    "/api/videos": { GET: api(async () => json({ videos: await videos() })) },
    "/api/models": {
      GET: api(async () => json(await myModels())),
      POST: api(async (req) => json(await addModel(req))),
    },
    "/api/models/:id": {
      PATCH: api(async (req) => json(await myModels(["edit", modelParam(req), ...fieldArgs(await body(req, anyModelFields))]))),
      DELETE: api(async (req) => json(await myModels(["remove", modelParam(req)]))),
    },
    "/api/models/:id/photo": { GET: api(async (req) => new Response(Bun.file(await modelPhoto(modelParam(req))), { headers: { "cache-control": "private, max-age=31536000, immutable" } })) },
    "/api/videos/:name": {
      DELETE: api(async (req) => {
        const path = videoPath(req.params.name);
        await rm(path);
        await rm(path.replace(/\.mp4$/, "_metadata.json"), { force: true });
        await rm(path.replace(/\.mp4$/, "_project.json"), { force: true });
        await rm(join(OUTPUT, ".posters", req.params.name!.replace(/\.mp4$/, ".jpg")), { force: true });
        return json({ deleted: req.params.name });
      }),
    },
    "/api/videos/:name/project": { GET: api(async (req) => json(await videoProject(req.params.name))) },
    "/api/videos/:name/poster": { GET: api(async (req) => new Response(Bun.file(await poster(req.params.name)))) },
    "/api/videos/:name/file": {
      GET: api((req) => {
        const file = Bun.file(videoPath(req.params.name));
        const download = new URL(req.url).searchParams.has("download");
        return new Response(file, download ? { headers: { "content-disposition": `attachment; filename="${req.params.name}"` } } : undefined);
      }),
    },
  },
  fetch: () => json({ error: "not found" }, 404),
});

console.log(`Faceless Shorts is at ${server.url}${phone}`);
