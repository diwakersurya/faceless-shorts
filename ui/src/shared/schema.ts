import { z } from "zod";

/** A choice as the Python tool names it (a voice, a style, a preset...). */
const key = z.string().regex(/^[\w-]{1,60}$/);

/** Safe uploaded file name stored under output/.uploads/. */
export const uploadName = z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}$/);

/** Safe project folder name stored under output/.projects/. */
export const projectName = z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}$/);

/** One of your models, as the Python tool names its folder. */
export const modelId = z.string().regex(/^[a-z0-9][a-z0-9-]{0,39}$/);

/** The five steps the Python tool reports as "[n/5]". */
export const STAGES = ["Voiceover", "Visuals", "Subtitles", "Audio", "Final assembly"] as const;

export const stickSchema = z
  .object({
    action: z.string().max(40).optional(),
    other: z.string().max(40).optional(),
    character: z.string().max(24).optional(),
    other_character: z.string().max(24).optional(),
    bubble: z.string().max(24).optional(),
    other_bubble: z.string().max(24).optional(),
    headline: z.string().max(80).optional(),
  })
  .nullable()
  .optional();

export const sceneSchema = z.object({
  index: z.number().int().min(1).max(30),
  text: z.string().trim().min(1, "Scene text cannot be empty").max(600),
  visual_prompt: z.string().trim().max(600).default(""),
  motion: z.string().max(60).default("punch_zoom_in"),
  duration_estimate: z.number().positive().max(60).optional(),
  stick: stickSchema,
  featuring: z.array(modelId).max(10).nullable().optional(),
  seed: z.number().int().min(0).max(2147483647).nullable().optional(),
  video_engine: key.nullable().optional(),
  media_path: z.string().max(260).nullable().optional(),
  headline: z.string().max(120).nullable().optional(),
});
export type SceneDraft = z.infer<typeof sceneSchema>;

export const draftScriptSchema = z.object({
  title: z.string().trim().min(1).max(200),
  niche: z.string().trim().max(100).default("custom"),
  target_duration: z.number().int().min(5).max(180).default(45),
  hook: z.string().trim().max(400).default(""),
  scenes: z.array(sceneSchema).min(1, "Add at least one scene").max(20),
  cta: z.string().trim().max(300).default(""),
  tags: z.array(z.string().max(60)).max(30).default([]),
});
export type DraftScript = z.infer<typeof draftScriptSchema>;

export const scriptRequestSchema = z.object({
  source: z.enum(["topic", "preset"]),
  topic: z.string().trim().max(1500).default(""),
  preset: z.string().default(""),
  language: key.default("en"),
  scriptEngine: key.default("auto"),
  sceneCount: z.number().int().min(2).max(12).nullable().optional(),
  tone: z.string().trim().max(200).default(""),
  stickman: z.boolean().default(false),
});
export type ScriptRequest = z.infer<typeof scriptRequestSchema>;

/** One render, as the form sends it and the server checks it. */
export const renderRequest = z
  .object({
    source: z.enum(["topic", "preset", "custom"]),
    topic: z.string().trim().max(200),
    preset: z.string(),
    script: draftScriptSchema.nullable().optional(),
    sceneCount: z.number().int().min(2).max(12).nullable().optional(),
    tone: z.string().trim().max(200).optional(),
    language: key,
    voice: key,
    subtitleStyle: key,
    artStyle: key,
    transition: key,
    effects: key,
    vfx: key.optional(),
    quality: key,
    music: key,
    speechRate: z.string().regex(/^[+-]\d{1,2}%$/).optional(),
    musicVolume: z.number().min(-40).max(0).optional(),
    whoosh: z.boolean().nullable().optional(),
    voiceStyle: z.string().trim().max(160).optional(),
    subtitlesEnabled: z.boolean().optional(),
    subtitlePosition: key.optional(),
    wordsPerScreen: z.number().int().min(1).max(6).nullable().optional(),
    watermark: z.string().trim().max(60).optional(),
    hookTitle: z.string().trim().max(100).optional(),
    customMusicFile: uploadName.nullable().optional(),
    customVoiceoverFile: uploadName.nullable().optional(),
    projectName: projectName.nullable().optional(),
    reuseVisuals: z.boolean().optional(),
    regenerateScenes: z.array(z.number().int().min(1).max(20)).max(20).optional(),
    scriptEngine: key,
    ttsEngine: key,
    imageEngine: key,
    videoEngine: key,
    /** Your models in every scene (the Python tool says how many a short may have). */
    featuring: z.array(modelId).max(10),
    progressBar: z.boolean(),
  })
  .superRefine((r, ctx) => {
    if (r.source === "topic" && r.topic.length < 3) ctx.addIssue({ code: "custom", path: ["topic"], message: "Describe the topic in at least 3 characters" });
    if (r.source === "preset" && !key.safeParse(r.preset).success) ctx.addIssue({ code: "custom", path: ["preset"], message: "Pick a preset" });
    if (r.source === "custom" && (!r.script || r.script.scenes.length === 0)) ctx.addIssue({ code: "custom", path: ["script"], message: "Add at least one scene to your script" });
  });
export type RenderRequest = z.infer<typeof renderRequest>;

/** A finished video's file name. It comes from the address bar and from requests, so it is always checked. */
export const videoName = z.string().regex(/^[\w-]{1,120}\.mp4$/);

/** A model's name, kind and description. The limits come from the Python tool (options.myModels), which checks them again. */
export const modelFields = (limits: { maxName: number; maxDescription: number }) =>
  z.object({
    name: z.string().trim().min(1, "Give the model a name").max(limits.maxName, `At most ${limits.maxName} characters`),
    kind: key,
    description: z.string().trim().max(limits.maxDescription, `At most ${limits.maxDescription} characters`),
  });
export type ModelFields = z.infer<ReturnType<typeof modelFields>>;
/** What the server accepts before the Python tool applies the exact limits. */
export const anyModelFields = modelFields({ maxName: 100, maxDescription: 1000 });

export const settings = z.object({
  fastmetalHome: z
    .string()
    .trim()
    .max(500)
    .refine((v) => v === "" || v.startsWith("/") || v.startsWith("~"), "Give the full path, starting with / or ~"),
  /** Left out: the saved key stays as it is. Empty: the saved key is forgotten. */
  geminiKey: z
    .string()
    .trim()
    .max(200)
    // Google checks the key when it is saved (config --gemini-key); here only spaces inside it are refused.
    // Keys are not always letters, digits, - and _: newer ones look like "AQ.Ab8…".
    .regex(/^\S*$/, "A Gemini API key has no spaces in it")
    .optional(),
});
export type Settings = z.infer<typeof settings>;
