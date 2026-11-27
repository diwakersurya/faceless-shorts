import { z } from "zod";

/** A choice as the Python tool names it (a voice, a style, a preset...). */
const key = z.string().regex(/^[\w-]{1,60}$/);

/** One of your models, as the Python tool names its folder. */
export const modelId = z.string().regex(/^[a-z0-9][a-z0-9-]{0,39}$/);

/** The five steps the Python tool reports as "[n/5]". */
export const STAGES = ["Voiceover", "Visuals", "Subtitles", "Audio", "Final assembly"] as const;

/** One render, as the form sends it and the server checks it. */
export const renderRequest = z
  .object({
    source: z.enum(["topic", "preset"]),
    topic: z.string().trim().max(200),
    preset: z.string(),
    language: key,
    voice: key,
    subtitleStyle: key,
    artStyle: key,
    transition: key,
    effects: key,
    quality: key,
    music: key,
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
    .regex(/^[\w-]*$/, "A Gemini API key has only letters, digits, - and _")
    .optional(),
});
export type Settings = z.infer<typeof settings>;
