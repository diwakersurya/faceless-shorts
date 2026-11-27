#!/usr/bin/env bun
// The web app's server: serves the page, and runs the Python tool for it one render at a time.
import { existsSync } from "node:fs";
import { mkdir, readdir, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { allowed } from "./access";
import { type Progress, START, buildArgs, failureReason, outputName, parseLine } from "./render";
import { type RenderRequest, anyModelFields, modelId, renderRequest, settings as settingsSchema, videoName } from "./shared/schema";
import app from "./web/index.html";

const UI = resolve(import.meta.dir, "..");
const REPO = resolve(UI, ".."); // the Python tool runs from the repository root
const OUTPUT = join(REPO, "output");
const CONFIG = join(UI, "config.json");

const defaults = {
  port: 4780,
  host: "127.0.0.1", // answers only on this computer
  python: "python3", // the Python that has faceless_shorts' requirements installed
  // Other names this computer may be reached by (a tunnel you run yourself). --tailnet adds the Tailscale name by itself.
  allowedHosts: [] as string[],
};
const saved = (await Bun.file(CONFIG).exists()) ? await Bun.file(CONFIG).json() : {};
const cfg: typeof defaults = { ...defaults, ...saved };

const json = (body: unknown, status = 200) => Response.json(body, { status });
const fail = (status: number, message: string) => Object.assign(new Error(message), { status });
const lastLine = (text: string) => text.trim().split("\n").at(-1) ?? "";

/** This computer's name on the tailnet (machine.tailnet.ts.net) and how to call Tailscale, or what is in the way. */
async function tailnet(): Promise<{ name: string; bin: string } | { problem: string }> {
  // The Mac app keeps its command inside the app, so it is often not on the PATH.
  const inApp = "/Applications/Tailscale.app/Contents/MacOS/Tailscale";
  const bin = Bun.which("tailscale") ? "tailscale" : existsSync(inApp) ? inApp : null;
  if (!bin) return { problem: "Tailscale is not installed on this computer. Install it from https://tailscale.com/download and sign in, then run this again." };
  const res = await Bun.$`${bin} status --json`.quiet().nothrow();
  try {
    const status = JSON.parse(res.stdout.toString());
    const name = (status.Self?.DNSName as string | undefined)?.replace(/\.$/, "");
    // A computer keeps its tailnet name while Tailscale is switched off, but nothing can reach it then.
    if (status.BackendState !== "Running") return { problem: "Tailscale is switched off on this computer. Switch it on (and sign in if it asks), then run this again." };
    if (name) return { name, bin };
  } catch {} // no status: treated as not signed in
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

// progress lines arrive as they are printed
const pythonEnv = () => ({ ...process.env, PYTHONUNBUFFERED: "1" });

// ---------------------------------------------------------------- choices
// The Python tool owns every list of choices; the page only shows them.
// ponytail: asked again at most every 30 s (and at once after a settings change).
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

// ------------------------------------------------------------------ render
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
// One render at a time: the Python tool keeps its working files in one directory, and the video model needs the whole GPU.
let job: Job | null = null;
let running: Bun.Subprocess | null = null;

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

async function start(request: RenderRequest) {
  if (job?.state === "running") throw fail(409, "A short is being made already. Wait for it or stop it first.");
  await mkdir(OUTPUT, { recursive: true });
  const video = outputName(request);
  const mine: Job = {
    id: crypto.randomUUID(),
    state: "running",
    title: request.source === "preset" ? request.preset.replace(/_/g, " ") : request.topic,
    request,
    startedAt: Date.now(),
    finishedAt: null,
    progress: START,
    log: [],
    video,
    error: null,
  };
  const proc = Bun.spawn([cfg.python, ...buildArgs(request, join("output", video))], { cwd: REPO, env: pythonEnv(), stdout: "pipe", stderr: "pipe" });
  job = mine;
  running = proc;
  const onLine = (raw: string) => {
    const line = raw.replace(/\x1b\[[0-9;]*m/g, "").trimEnd();
    if (!line.trim()) return;
    mine.log.push(line);
    if (mine.log.length > 400) mine.log.shift();
    mine.progress = parseLine(line, mine.progress);
  };
  void Promise.all([pump(proc.stdout, onLine), pump(proc.stderr, onLine), proc.exited]).then(([, , code]) => {
    if (running === proc) running = null;
    mine.finishedAt = Date.now();
    if (mine.state === "cancelled") return;
    if (code === 0 && existsSync(join(OUTPUT, video))) mine.state = "done";
    else {
      mine.state = "failed";
      mine.error = failureReason(mine.log);
    }
  });
  return mine;
}

/** The Python tool starts the video model as its own process, so the whole family is stopped, parent first. */
async function stopTree(pid: number) {
  const family = [pid];
  for (const parent of family) {
    const kids = await Bun.$`pgrep -P ${parent}`.nothrow().quiet().text();
    family.push(...kids.split("\n").filter(Boolean).map(Number));
  }
  for (const member of family) {
    try {
      process.kill(member, "SIGTERM");
    } catch {} // gone already
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

// ------------------------------------------------------------------ videos
async function videos() {
  const names = existsSync(OUTPUT) ? (await readdir(OUTPUT)).filter((n) => videoName.safeParse(n).success) : [];
  const rows = await Promise.all(
    names.map(async (name) => {
      const info = await stat(join(OUTPUT, name));
      // The Python tool writes titles and tags for the platforms next to each video.
      const meta = await Bun.file(join(OUTPUT, name.replace(/\.mp4$/, "_metadata.json")))
        .json()
        .catch(() => null);
      return {
        name,
        size: info.size,
        createdAt: Math.round(info.mtimeMs / 1000),
        title: (meta?.youtube?.title as string | undefined) ?? null,
        caption: (meta?.tiktok?.caption as string | undefined) ?? null,
        tags: (meta?.youtube?.tags as string[] | undefined) ?? [],
      };
    }),
  );
  // A file still being written is not a video yet.
  const unfinished = job?.state === "running" ? job.video : null;
  return rows.filter((v) => v.size > 0 && v.name !== unfinished).sort((a, b) => b.createdAt - a.createdAt);
}

/** A still from the video, made once with ffmpeg and kept. Lists use it, so a phone does not open every video to show them. */
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
// The Python tool keeps them (photos of people, outside the repository) and checks every change; this only passes them on.
type MyModel = { id: string; name: string; kind: string; description: string; created: number };
let modelsFolder: string | null = null; // where the photos are, learned from the first answer
const PHOTO_TYPES: Record<string, string> = { "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp" };
const MAX_PHOTO = 20_000_000;

async function myModels(args: string[] = ["list"]) {
  const proc = Bun.spawn([cfg.python, "-m", "faceless_shorts", "models", ...args, "--json"], { cwd: REPO, env: pythonEnv(), stdout: "pipe", stderr: "pipe" });
  const [stdout, stderr, code] = await Promise.all([new Response(proc.stdout).text(), new Response(proc.stderr).text(), proc.exited]);
  // A refusal (photo not readable, name taken...) is the last line the tool printed, written for a person.
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
  // The Python tool reads the photo from a file and keeps its own copy; this one is removed straight after.
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

// Two copies on one port would take turns answering, each with its own videos: refuse to be the second.
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
    // The Home Screen icon, at the fixed address web/manifest.webmanifest names.
    "/icon.png": () => new Response(Bun.file(join(import.meta.dir, "web/icon.png"))),
    "/api/options": {
      GET: api(async () => {
        const all = await options();
        return json({ ...all, settings: { fastmetalHome: all.fastmetal.configured } });
      }),
    },
    "/api/settings": {
      // Where FastMetal is installed and the Gemini key are facts about this computer, so the Python tool
      // keeps them (in its own settings file): the command line and every copy of this app then agree.
      PUT: api(async (req) => {
        const { fastmetalHome, geminiKey } = await body(req, settingsSchema);
        // The key goes in on standard input, never on the command line, where other programs could read it.
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
        choices = null; // which engines can be used depends on these
        return json({ fastmetalHome }); // the key is never sent back
      }),
    },
    "/api/job": {
      GET: api(() => json({ job })),
      POST: api(async (req) => json({ job: await start(await body(req, renderRequest)) })),
      DELETE: api(async () => json({ job: await cancel() })),
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
    // A new photo means a new model, so a photo never changes under its address: the page adds ?v=created.
    "/api/models/:id/photo": { GET: api(async (req) => new Response(Bun.file(await modelPhoto(modelParam(req))), { headers: { "cache-control": "private, max-age=31536000, immutable" } })) },
    "/api/videos/:name": {
      DELETE: api(async (req) => {
        const path = videoPath(req.params.name);
        await rm(path);
        await rm(path.replace(/\.mp4$/, "_metadata.json"), { force: true });
        await rm(join(OUTPUT, ".posters", req.params.name!.replace(/\.mp4$/, ".jpg")), { force: true });
        return json({ deleted: req.params.name });
      }),
    },
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
