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
  featuring: [],
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

test("featured models become one --featuring list, and only real model ids are accepted", () => {
  expect(buildArgs(base, "o.mp4").some((a) => a.startsWith("--featuring"))).toBe(false);
  expect(buildArgs({ ...base, featuring: ["ravi", "red-bike"] }, "o.mp4")).toContain("--featuring=ravi,red-bike");
  expect(renderRequest.safeParse({ ...base, featuring: ["../etc"] }).success).toBe(false);
  expect(renderRequest.safeParse({ ...base, featuring: ["-x"] }).success).toBe(false);
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

test("custom storyboard, audio, caption, and fast re-assembly flags are passed cleanly", () => {
  const custom: RenderRequest = {
    ...base,
    source: "custom",
    script: {
      title: "My Directed Short",
      niche: "custom",
      target_duration: 30,
      hook: "Watch this",
      scenes: [{ index: 1, text: "Scene one.", visual_prompt: "neon alley", motion: "pan_left", seed: 77 }],
      cta: "Follow",
      tags: ["#shorts"],
    },
    vfx: "particles",
    speechRate: "+15%",
    musicVolume: -14,
    whoosh: true,
    subtitlesEnabled: false,
    subtitlePosition: "top",
    wordsPerScreen: 2,
    watermark: "@diwaker",
    hookTitle: "DON'T MISS THIS",
    reuseVisuals: true,
    regenerateScenes: [2, 4],
  };
  expect(renderRequest.safeParse(custom).success).toBe(true);
  expect(outputName(custom, new Date(2026, 10, 28, 8, 0, 0))).toBe("my-directed-short-20261128-080000.mp4");
  const args = buildArgs(custom, "output/out.mp4", {
    scriptJsonPath: "output/.projects/p/draft_script.json",
    projectDir: "output/.projects/p",
    musicFilePath: "output/.uploads/song.mp3",
    voiceoverPath: "output/.uploads/voice.wav",
  });
  expect(args).toEqual(
    expect.arrayContaining([
      "--script-json=output/.projects/p/draft_script.json",
      "--vfx=particles",
      "--rate=+15%",
      "--music-volume=-14",
      "--whoosh",
      "--no-subtitles",
      "--subtitle-position=top",
      "--words-per-screen=2",
      "--watermark=@diwaker",
      "--hook-title=DON'T MISS THIS",
      "--music-file=output/.uploads/song.mp3",
      "--voiceover=output/.uploads/voice.wav",
      "--project-dir=output/.projects/p",
      "--reuse-visuals",
      "--regenerate-scene=2",
      "--regenerate-scene=4",
    ]),
  );
});
