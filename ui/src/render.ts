// Turning a request into the Python command, and the Python tool's output into progress.
import type { RenderRequest } from "./shared/schema";

/** "why-foxes-hunt-20261126-091500.mp4": readable, and never the name of an earlier video. */
export function outputName(req: RenderRequest, now = new Date()) {
  const text = req.source === "preset" ? req.preset : req.topic;
  const slug = text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/, "");
  const two = (n: number) => String(n).padStart(2, "0");
  // Local time, so the name matches the clock of the person who made it.
  const stamp = `${now.getFullYear()}${two(now.getMonth() + 1)}${two(now.getDate())}-${two(now.getHours())}${two(now.getMinutes())}${two(now.getSeconds())}`;
  return `${slug || "short"}-${stamp}.mp4`;
}

/** Arguments for `python`. Every value is joined to its flag with "=", so a topic that starts with "-" stays a value. */
export function buildArgs(req: RenderRequest, outputPath: string) {
  return [
    "-m",
    "faceless_shorts",
    "generate",
    req.source === "preset" ? `--niche=${req.preset}` : `--topic=${req.topic}`,
    `--language=${req.language}`,
    `--voice=${req.voice}`,
    `--style=${req.subtitleStyle}`,
    `--art-style=${req.artStyle}`,
    `--transition=${req.transition}`,
    `--effects=${req.effects}`,
    `--quality=${req.quality}`,
    `--music=${req.music}`,
    `--script-engine=${req.scriptEngine}`,
    `--tts-engine=${req.ttsEngine}`,
    `--engine=${req.imageEngine}`,
    `--video-engine=${req.videoEngine}`,
    ...(req.featuring.length ? [`--featuring=${req.featuring.join(",")}`] : []),
    ...(req.progressBar ? [] : ["--no-progress-bar"]),
    `--output=${outputPath}`,
  ];
}

/** stage: 0 before the first step, then 1 to 5. scene/scenes: where the Visuals step is, when the tool says. */
export type Progress = { stage: number; scene: number | null; scenes: number | null };
export const START: Progress = { stage: 0, scene: null, scenes: null };

/** Reads one line the Python tool printed. Lines that say nothing about progress leave it as it was. */
export function parseLine(line: string, p: Progress): Progress {
  const stage = line.match(/\[(\d)(?:\.\d)?\/5\]/); // "[2/5]", and "[2.5/5]" for the effects pass
  if (stage) return { ...p, stage: Number(stage[1]) };
  const total = line.match(/Scenes:\s*(\d+)/); // from the brief printed before the work starts
  if (total) return { ...p, scenes: Number(total[1]) };
  const scene = line.match(/Scene (\d+)(?:\/(\d+))?: (?:Generating|Rendering motion)/);
  if (scene) return { ...p, scene: Number(scene[1]), scenes: scene[2] ? Number(scene[2]) : p.scenes };
  return p;
}

/** The line that says why a render failed: the Python exception if there is one, else the last thing printed. */
export function failureReason(log: string[]) {
  const lines = log.map((l) => l.trim()).filter(Boolean);
  return lines.findLast((l) => /^\w+(Error|Exception):/.test(l)) ?? lines.at(-1) ?? "The render stopped without saying why.";
}
