#!/usr/bin/env bun
// The web app's server: serves the page, and runs the Python tool for it one render at a time.
import { existsSync } from "node:fs";
import { mkdir, readdir, rm, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { type Progress, START, buildArgs, failureReason, outputName, parseLine } from "./render";
import { type RenderRequest, renderRequest, settings as settingsSchema, videoName } from "./shared/schema";
import app from "./web/index.html";

const UI = resolve(import.meta.dir, "..");
const REPO = resolve(UI, ".."); // the Python tool runs from the repository root
const OUTPUT = join(REPO, "output");
const CONFIG = join(UI, "config.json");

const defaults = {
  port: 4780,
  host: "127.0.0.1", // answers only on this computer
  python: "python3", // the Python that has faceless_shorts' requirements installed
  fastmetalHome: "", // set from the app's Settings; empty = the Python tool's own default
};
const saved = (await Bun.file(CONFIG).exists()) ? await Bun.file(CONFIG).json() : {};
const cfg: typeof defaults = { ...defaults, ...saved };

const json = (body: unknown, status = 200) => Response.json(body, { status });
const fail = (status: number, message: string) => Object.assign(new Error(message), { status });
const expand = (path: string) => (path.startsWith("~") ? join(homedir(), path.slice(1)) : path);

/**
 * Who may call the API: a page served by this server, on this computer. A request addressed to
 * another host name (DNS rebinding) or made by another website (its Origin differs) is refused.
 */
function allowed(req: Request) {
  const url = new URL(req.url);
  if (!["localhost", "127.0.0.1"].includes(url.hostname)) return false;
  const origin = req.headers.get("origin");
  return !origin || origin === url.origin;
}

const pythonEnv = () => ({
  ...process.env,
  PYTHONUNBUFFERED: "1", // progress lines arrive as they are printed
  ...(cfg.fastmetalHome ? { FACELESS_FASTMETAL_HOME: expand(cfg.fastmetalHome) } : {}),
});

// ---------------------------------------------------------------- choices
// The Python tool owns every list of choices; the page only shows them.
// ponytail: asked again at most every 30 s (and at once after a settings change).
let choices: { at: number; value: Record<string, unknown> } | null = null;
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

function videoPath(name: string | undefined) {
  const parsed = videoName.safeParse(name);
  if (!parsed.success) throw fail(400, "That is not a video name.");
  const path = join(OUTPUT, parsed.data);
  if (!existsSync(path)) throw fail(404, "That short is no longer there.");
  return path;
}

// ------------------------------------------------------------------ server
type Req = Bun.BunRequest<any>;
const api = (handler: (req: Req) => Response | Promise<Response>) => async (req: Req) => {
  if (!allowed(req)) return json({ error: "forbidden" }, 403);
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

const server = Bun.serve({
  hostname: cfg.host,
  port: cfg.port,
  development: process.argv.includes("--dev"),
  routes: {
    "/": app,
    "/api/options": { GET: api(async () => json({ ...(await options()), settings: { fastmetalHome: cfg.fastmetalHome } })) },
    "/api/settings": {
      PUT: api(async (req) => {
        const { fastmetalHome } = await body(req, settingsSchema);
        if (fastmetalHome && !existsSync(expand(fastmetalHome))) throw fail(400, `There is no folder at ${fastmetalHome}`);
        cfg.fastmetalHome = fastmetalHome;
        await Bun.write(CONFIG, JSON.stringify({ ...saved, fastmetalHome }, null, 2) + "\n");
        choices = null; // whether FastMetal is found depends on this folder
        return json({ fastmetalHome });
      }),
    },
    "/api/job": {
      GET: api(() => json({ job })),
      POST: api(async (req) => json({ job: await start(await body(req, renderRequest)) })),
      DELETE: api(async () => json({ job: await cancel() })),
    },
    "/api/videos": { GET: api(async () => json({ videos: await videos() })) },
    "/api/videos/:name": {
      DELETE: api(async (req) => {
        const path = videoPath(req.params.name);
        await rm(path);
        await rm(path.replace(/\.mp4$/, "_metadata.json"), { force: true });
        return json({ deleted: req.params.name });
      }),
    },
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

console.log(`Faceless Shorts is at ${server.url}`);
