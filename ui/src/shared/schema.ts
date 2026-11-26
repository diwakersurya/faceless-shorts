import { z } from "zod";

/** A choice as the Python tool names it (a voice, a style, a preset...). */
const key = z.string().regex(/^[\w-]{1,60}$/);

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
    imageEngine: key,
    videoEngine: key,
    progressBar: z.boolean(),
  })
  .superRefine((r, ctx) => {
    if (r.source === "topic" && r.topic.length < 3) ctx.addIssue({ code: "custom", path: ["topic"], message: "Describe the topic in at least 3 characters" });
    if (r.source === "preset" && !key.safeParse(r.preset).success) ctx.addIssue({ code: "custom", path: ["preset"], message: "Pick a preset" });
  });
export type RenderRequest = z.infer<typeof renderRequest>;

/** A finished video's file name. It comes from the address bar and from requests, so it is always checked. */
export const videoName = z.string().regex(/^[\w-]{1,120}\.mp4$/);

export const settings = z.object({
  fastmetalHome: z
    .string()
    .trim()
    .max(500)
    .refine((v) => v === "" || v.startsWith("/") || v.startsWith("~"), "Give the full path, starting with / or ~"),
});
export type Settings = z.infer<typeof settings>;
