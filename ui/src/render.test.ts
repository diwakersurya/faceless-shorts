import { expect, test } from "bun:test";
import { allowed } from "./access";
import { START, buildArgs, failureReason, outputName, parseLine } from "./render";
import { type RenderRequest, renderRequest, videoName } from "./shared/schema";

const base: RenderRequest = {
  source: "topic",
  topic: "Why foxes hunt at dawn",
  preset: "",
  language: "en",
  voice: "christopher",
  subtitleStyle: "hormozi",
  artStyle: "hyperreal",
  transition: "flash",
  effects: "none",
  quality: "social",
  music: "none",
  scriptEngine: "gemini",
  ttsEngine: "edge",
  imageEngine: "pollinations",
  videoEngine: "fastmetal",
  progressBar: true,
};

test("output name is a safe, unique file name", () => {
  const at = new Date(2026, 10, 26, 9, 15, 0); // local time
  const name = outputName(base, at);
  expect(name).toBe("why-foxes-hunt-at-dawn-20261126-091500.mp4");
  expect(videoName.safeParse(name).success).toBe(true);
  expect(outputName({ ...base, topic: "काला छेद" }, at)).toBe("short-20261126-091500.mp4");
  expect(videoName.safeParse("../secret.mp4").success).toBe(false);
});

test("arguments keep a leading dash inside the value", () => {
  const args = buildArgs({ ...base, topic: "--offline tricks" }, "output/x.mp4");
  expect(args).toContain("--topic=--offline tricks");
  expect(args).toContain("--video-engine=fastmetal");
  expect(args).toContain("--script-engine=gemini");
  expect(args).toContain("--tts-engine=edge");
  expect(args).not.toContain("--no-progress-bar");
  expect(buildArgs({ ...base, source: "preset", preset: "stoicism", progressBar: false }, "o.mp4")).toEqual(expect.arrayContaining(["--niche=stoicism", "--no-progress-bar"]));
});

test("a request needs a topic or a preset", () => {
  expect(renderRequest.safeParse(base).success).toBe(true);
  expect(renderRequest.safeParse({ ...base, topic: " a " }).success).toBe(false);
  expect(renderRequest.safeParse({ ...base, source: "preset" }).success).toBe(false);
  expect(renderRequest.safeParse({ ...base, voice: "x; rm -rf" }).success).toBe(false);
});

test("progress is read from the tool's own output", () => {
  const lines = [
    "│ Scenes: 5                                │",
    "🚀 [1/5] Synthesizing continuous neural voiceover (christopher, language=en)...",
    "🎨 [2/5] Producing visuals | style='hyperreal' | engine='pollinations'",
    "    → Scene 2/5: Generating fastmetal video clip (3.83s, takes a few minutes)...",
    "✨ [2.5/5] Compositing VFX & Animation Suite (particles)...",
    "    → Scene 4: Rendering motion (4.20s, pan_left)...",
    "🎬 [5/5] Final assembly: Post-FX grade",
  ];
  const seen = lines.map(((p) => (line: string) => (p = parseLine(line, p)))(START));
  expect(seen[0]).toEqual({ stage: 0, scene: null, scenes: 5 });
  expect(seen[3]).toEqual({ stage: 2, scene: 2, scenes: 5 });
  expect(seen[4]!.stage).toBe(2);
  expect(seen[5]).toEqual({ stage: 2, scene: 4, scenes: 5 });
  expect(seen[6]!.stage).toBe(5);
});

test("the failure reason is the Python exception, not ffmpeg's last words", () => {
  expect(failureReason(["Traceback (most recent call last):", "RuntimeError: FFmpeg final assembly failed (code 234):", "Conversion failed!", ""])).toBe("RuntimeError: FFmpeg final assembly failed (code 234):");
  expect(failureReason(["zsh: command not found"])).toBe("zsh: command not found");
});

test("the API answers its own page, the tailnet name when told, and nobody else", () => {
  const tailnet = ["mac.tail1234.ts.net"];
  const req = (url: string, origin?: string) => new Request(url, origin ? { headers: { origin } } : undefined);
  expect(allowed(req("http://127.0.0.1:4780/api/job"))).toBe(true);
  expect(allowed(req("http://127.0.0.1:4780/api/job", "http://127.0.0.1:4780"))).toBe(true);
  // another website calling the local server, and a foreign name pointed at it (DNS rebinding)
  expect(allowed(req("http://127.0.0.1:4780/api/job", "https://evil.example"))).toBe(false);
  expect(allowed(req("http://evil.example:4780/api/job"))).toBe(false);
  // the tailnet name is refused unless the server was started for it
  expect(allowed(req("http://mac.tail1234.ts.net/api/job", "https://mac.tail1234.ts.net"))).toBe(false);
  expect(allowed(req("http://mac.tail1234.ts.net/api/job", "https://mac.tail1234.ts.net"), tailnet)).toBe(true);
  expect(allowed(req("http://mac.tail1234.ts.net/api/job", "https://evil.example"), tailnet)).toBe(false);
});
