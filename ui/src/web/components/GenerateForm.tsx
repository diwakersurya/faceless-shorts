import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { Collapsible } from "@astryxdesign/core/Collapsible";
import { FileInput } from "@astryxdesign/core/FileInput";
import { Grid } from "@astryxdesign/core/Grid";
import { HStack } from "@astryxdesign/core/HStack";
import { Heading } from "@astryxdesign/core/Heading";
import { MultiSelector } from "@astryxdesign/core/MultiSelector";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
import { Selector } from "@astryxdesign/core/Selector";
import { Skeleton } from "@astryxdesign/core/Skeleton";
import { Switch } from "@astryxdesign/core/Switch";
import { Text } from "@astryxdesign/core/Text";
import { TextInput } from "@astryxdesign/core/TextInput";
import { VStack } from "@astryxdesign/core/VStack";
import { FileEdit, ListPlus, Sparkles, UserPlus } from "lucide-react";
import { useState } from "react";
import { type DraftScript, type RenderRequest, renderRequest } from "../../shared/schema";
import type { Choice, Engine, MyModel, Options, SavedProject } from "../api";
import { useDraftScript, useMyModels, useStartRender, useUploadFile } from "../queries";
import { toast } from "../toast";
import { StoryboardDialog } from "./StoryboardDialog";
import { StylePreview } from "./StylePreview";
import { VoicePreviewButton } from "./VoicePreviewButton";

const STORE = "faceless-shorts-ui.form";
const FORM_HEIGHT = 620;

const SPEECH_RATES = [
  { value: "-15%", label: "Slow (-15%)" },
  { value: "-5%", label: "Slightly slow (-5%)" },
  { value: "+0%", label: "Natural (+0%)" },
  { value: "+8%", label: "Snappy (+8%)" },
  { value: "+15%", label: "Fast (+15%)" },
  { value: "+25%", label: "Very fast (+25%)" },
];

const MUSIC_VOLUMES = [
  { value: "-26", label: "Subtle (-26 dB)" },
  { value: "-22", label: "Soft (-22 dB)" },
  { value: "-18", label: "Balanced (-18 dB)" },
  { value: "-14", label: "Present (-14 dB)" },
  { value: "-10", label: "Bold (-10 dB)" },
];

const SCENE_COUNTS = [
  { value: "auto", label: "Auto" },
  ...[3, 4, 5, 6, 7, 8, 10].map((n) => ({ value: String(n), label: `${n} scenes` })),
];

const WORDS_PER_SCREEN = [
  { value: "auto", label: "Style default" },
  { value: "1", label: "1 word" },
  { value: "2", label: "2 words" },
  { value: "3", label: "3 words" },
  { value: "4", label: "4 words" },
];

const BLANK_SCRIPT: DraftScript = {
  title: "My Custom Short",
  niche: "custom",
  target_duration: 35,
  hook: "",
  scenes: [
    { index: 1, text: "", visual_prompt: "", motion: "punch_zoom_in" },
    { index: 2, text: "", visual_prompt: "", motion: "dramatic_zoom_out" },
    { index: 3, text: "", visual_prompt: "", motion: "pan_left" },
  ],
  cta: "",
  tags: ["#shorts"],
};

function initial(options: Options, loaded?: SavedProject | null): RenderRequest {
  let last: Partial<Record<keyof RenderRequest, unknown>> = {};
  if (loaded) {
    last = { ...(loaded.settings as Record<string, unknown>), source: "custom", script: loaded.script, projectName: loaded.projectName, reuseVisuals: true };
  } else {
    try {
      last = JSON.parse(localStorage.getItem(STORE) ?? "{}") ?? {};
    } catch {}
  }
  const d = options.defaults;
  const pick = (value: unknown, list: Choice[], fallback: string) => (list.some((c) => c.value === value) ? (value as string) : fallback);
  const usable = (value: unknown, list: Engine[], fallback: string) => pick(value, list.filter((e) => e.available), fallback);
  const language = pick(last.language, options.languages, d.language);
  const spoken = options.languages.find((l) => l.value === language);
  const ttsEngine = usable(last.ttsEngine, options.ttsEngines, d.ttsEngine);
  const voice =
    ttsEngine === "gemini"
      ? pick(last.voice, options.gemini.voices, options.gemini.defaultVoice)
      : spoken?.voices.includes(last.voice as string)
        ? (last.voice as string)
        : (spoken?.defaultVoice ?? d.voice);
  return {
    source: last.source === "preset" ? "preset" : last.source === "custom" ? "custom" : "topic",
    topic: typeof last.topic === "string" ? last.topic : "",
    preset: pick(last.preset, options.presets, options.presets[0]?.value ?? ""),
    script: (last.script as DraftScript | null | undefined) ?? null,
    sceneCount: typeof last.sceneCount === "number" ? last.sceneCount : null,
    tone: typeof last.tone === "string" ? last.tone : "",
    language,
    voice,
    subtitleStyle: pick(last.subtitleStyle, options.subtitleStyles, d.subtitleStyle),
    artStyle: pick(last.artStyle, options.artStyles, d.artStyle),
    transition: pick(last.transition, options.transitions, d.transition),
    effects: pick(last.effects, options.effects, d.effects),
    vfx: pick(last.vfx, options.vfx ?? [], "none"),
    quality: pick(last.quality, options.qualities, d.quality),
    music: options.music.includes(last.music as string) ? (last.music as string) : d.music,
    speechRate: typeof last.speechRate === "string" ? last.speechRate : "+0%",
    musicVolume: typeof last.musicVolume === "number" ? last.musicVolume : -18,
    whoosh: typeof last.whoosh === "boolean" ? last.whoosh : null,
    voiceStyle: typeof last.voiceStyle === "string" ? last.voiceStyle : "",
    subtitlesEnabled: typeof last.subtitlesEnabled === "boolean" ? last.subtitlesEnabled : true,
    subtitlePosition: pick(last.subtitlePosition, options.subtitlePositions ?? [], "auto"),
    wordsPerScreen: typeof last.wordsPerScreen === "number" ? last.wordsPerScreen : null,
    watermark: typeof last.watermark === "string" ? last.watermark : "",
    hookTitle: typeof last.hookTitle === "string" ? last.hookTitle : "",
    customMusicFile: typeof last.customMusicFile === "string" ? last.customMusicFile : null,
    customVoiceoverFile: typeof last.customVoiceoverFile === "string" ? last.customVoiceoverFile : null,
    projectName: typeof last.projectName === "string" ? last.projectName : null,
    reuseVisuals: typeof last.reuseVisuals === "boolean" ? last.reuseVisuals : false,
    regenerateScenes: Array.isArray(last.regenerateScenes) ? (last.regenerateScenes as number[]) : [],
    scriptEngine: usable(last.scriptEngine, options.scriptEngines, d.scriptEngine),
    ttsEngine,
    imageEngine: usable(last.imageEngine, options.imageEngines, d.imageEngine),
    videoEngine: usable(last.videoEngine, options.videoEngines, d.videoEngine),
    featuring: Array.isArray(last.featuring) ? last.featuring.filter((id): id is string => typeof id === "string") : [],
    progressBar: typeof last.progressBar === "boolean" ? last.progressBar : true,
  };
}

const engineChoices = (list: Engine[]) => list.map((e) => ({ value: e.value, label: e.label, description: e.description ?? undefined, disabled: !e.available }));
const words = (key: string) => key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

function featuringNote(options: Options, form: RenderRequest, chosen: MyModel[]) {
  const image = options.imageEngines.find((e) => e.value === form.imageEngine);
  const video = options.videoEngines.find((e) => e.value === form.videoEngine);
  const seeing = options.imageEngines.filter((e) => e.usesPhoto && e.available).map((e) => e.label).join(" or ");
  const better = seeing ? ` For their likeness, choose ${seeing} for Images.` : "";
  if (form.videoEngine === "stickman") {
    const person = chosen.some((m) => m.kind !== "object");
    const thing = chosen.some((m) => m.kind === "object");
    return [person && "The stick figures wear their faces.", thing && "An object stands beside the figures as a picture."].filter(Boolean).join(" ");
  }
  if (video?.usesPhoto)
    return image?.usesPhoto
      ? `Each clip opens on a picture of them that ${image.label} draws from their photos.`
      : `Each clip opens on a picture by ${image?.label}, which cannot see photos and draws them from their descriptions.${better}`;
  if (form.videoEngine !== "none") return `${video?.label} cannot see photos: it draws them from their descriptions.`;
  return image?.usesPhoto ? `${image.label} draws them from their photos.` : `${image?.label} cannot see photos: it draws them from their descriptions.${better}`;
}

type FeaturingProps = { options: Options; form: RenderRequest; chosen: string[]; onChange: (featuring: string[]) => void; onModels: () => void };

function Featuring({ options, form, chosen, onChange, onModels }: FeaturingProps) {
  const models = useMyModels();
  const list = models.data?.models ?? [];
  const full = chosen.length >= options.myModels.maxFeatured;
  if (models.data && list.length === 0)
    return (
      <VStack gap={1}>
        <Text weight="medium">Featuring</Text>
        <Text size="sm" color="secondary">
          Put a person or an object from your own photo in every scene.
        </Text>
        <HStack>
          <Button label="Make a model from a photo" icon={<UserPlus size={16} />} size="sm" variant="secondary" onClick={onModels} />
        </HStack>
      </VStack>
    );
  return (
    <VStack gap={1}>
      <MultiSelector
        label="Featuring"
        isOptional
        description={chosen.length ? featuringNote(options, form, list.filter((m) => chosen.includes(m.id))) : `Your models to put in every scene, at most ${options.myModels.maxFeatured}.`}
        options={list.map((m) => ({
          value: m.id,
          label: m.name,
          description: options.myModels.kinds.find((k) => k.value === m.kind)?.label,
          disabled: full && !chosen.includes(m.id),
        }))}
        value={chosen}
        onChange={onChange}
        placeholder="None of your models"
        triggerDisplay="badges"
        hasClear
        isLoading={models.isLoading}
        status={models.isError ? { type: "error", message: `Your models could not be loaded: ${models.error.message}` } : undefined}
      />
      <HStack>
        <Button label="Manage your models" variant="ghost" size="sm" onClick={onModels} />
      </HStack>
    </VStack>
  );
}

type Props = {
  options: Options;
  busy: boolean;
  loadedProject?: SavedProject | null;
  onClearProject?: () => void;
  onSettings: () => void;
  onModels: () => void;
};

function Form({ options, busy, loadedProject, onClearProject, onSettings, onModels }: Props) {
  const start = useStartRender();
  const draft = useDraftScript();
  const upload = useUploadFile();
  const known = useMyModels().data?.models ?? [];
  const [form, setForm] = useState(() => initial(options, loadedProject));
  const [editorOpen, setEditorOpen] = useState(Boolean(loadedProject));
  const [musicLabel, setMusicLabel] = useState<string | null>(null);
  const [voiceoverLabel, setVoiceoverLabel] = useState<string | null>(null);

  const update = (patch: Partial<RenderRequest>) => {
    const next = { ...form, ...patch };
    setForm(next);
    // Keep lightweight preferences in localStorage (skip large inline custom scripts).
    const { script: _s, projectName: _p, reuseVisuals: _r, regenerateScenes: _rs, ...prefs } = next;
    localStorage.setItem(STORE, JSON.stringify(prefs));
  };

  const chosen = known.length ? form.featuring.filter((id) => known.some((m) => m.id === id)) : [];
  const parsed = renderRequest.safeParse({ ...form, featuring: chosen });
  const topicError = form.source === "topic" && form.topic.trim() && !parsed.success ? parsed.error.issues.find((i) => i.path[0] === "topic")?.message : undefined;
  const ready = parsed.success && !start.isPending;
  const language = options.languages.find((l) => l.value === form.language);
  const voices = form.ttsEngine === "gemini" ? options.gemini.voices : options.voices.filter((v) => language?.voices.includes(v.value));
  const selectedSubStyle = options.subtitleStyles.find((s) => s.value === form.subtitleStyle);
  const set = (field: keyof RenderRequest) => (value: string | null) => {
    if (value !== null) update({ [field]: value });
  };

  const handleDraft = async () => {
    if (form.source === "custom") {
      if (!form.script) update({ script: BLANK_SCRIPT });
      setEditorOpen(true);
      return;
    }
    if (form.source === "topic" && form.topic.trim().length < 3) {
      toast.bad("Describe the topic in at least 3 characters first.");
      return;
    }
    try {
      const res = await draft.mutateAsync({
        source: form.source,
        topic: form.topic,
        preset: form.preset,
        language: form.language,
        scriptEngine: form.scriptEngine,
        sceneCount: form.sceneCount ?? null,
        tone: form.tone || "",
        stickman: form.videoEngine === "stickman",
      });
      update({ source: "custom", script: res.script });
      setEditorOpen(true);
    } catch {}
  };

  const uploadAudio = async (picked: File | File[] | null, kind: "music" | "voiceover") => {
    const file = Array.isArray(picked) ? picked[0] : picked;
    if (!file) return;
    try {
      const res = await upload.mutateAsync(file);
      if (kind === "music") {
        update({ customMusicFile: res.file });
        setMusicLabel(res.name);
        toast.ok(`Custom music ready: ${res.name}`);
      } else {
        update({ customVoiceoverFile: res.file });
        setVoiceoverLabel(res.name);
        toast.ok(`Custom voiceover ready: ${res.name}`);
      }
    } catch {}
  };

  return (
    <Card>
      <form
        action={() => {
          if (ready) start.mutate(parsed.data);
        }}
      >
        <VStack gap={4}>
          <HStack justify="between" align="center">
            <Heading level={2}>{form.projectName ? "Edit short" : "New short"}</Heading>
            {form.projectName && (
              <Button
                label="Start fresh"
                size="sm"
                variant="ghost"
                onClick={() => {
                  onClearProject?.();
                  update({ projectName: null, reuseVisuals: false, regenerateScenes: [], source: "topic", script: null });
                }}
              />
            )}
          </HStack>

          <SegmentedControl
            label="What the short is about"
            layout="fill"
            value={form.source}
            onChange={(source) => update({ source: source as RenderRequest["source"] })}
          >
            <SegmentedControlItem value="topic" label="Your topic" />
            <SegmentedControlItem value="preset" label="Preset" />
            <SegmentedControlItem value="custom" label="Storyboard" />
          </SegmentedControl>

          {form.source === "topic" && (
            <VStack gap={3}>
              <TextInput
                label="Topic"
                value={form.topic}
                onChange={(topic) => update({ topic })}
                placeholder="Why foxes hunt at dawn"
                description="A script is written for it, or click Draft & edit scenes to review first."
                autoComplete="off"
                status={topicError ? { type: "error", message: topicError } : undefined}
              />
              <Grid columns={2} gap={3}>
                <Selector
                  label="Length"
                  options={SCENE_COUNTS}
                  value={form.sceneCount ? String(form.sceneCount) : "auto"}
                  onChange={(v) => update({ sceneCount: v && v !== "auto" ? Number(v) : null })}
                />
                <TextInput
                  label="Tone / direction"
                  value={form.tone || ""}
                  onChange={(tone) => update({ tone })}
                  placeholder="e.g. funny, dark, tutorial"
                />
              </Grid>
              <Button
                label="Draft & edit scenes first"
                variant="secondary"
                size="sm"
                icon={<FileEdit size={15} />}
                isLoading={draft.isPending}
                onClick={handleDraft}
              />
            </VStack>
          )}

          {form.source === "preset" && (
            <VStack gap={2}>
              <Selector
                label="Ready-made script"
                options={options.presets.map((p) => ({ value: p.value, label: p.label, description: `${p.scenes} scenes` }))}
                value={form.preset}
                onChange={set("preset")}
              />
              <Button
                label="Customize this preset's scenes"
                variant="ghost"
                size="sm"
                icon={<FileEdit size={14} />}
                isLoading={draft.isPending}
                onClick={handleDraft}
              />
            </VStack>
          )}

          {form.source === "custom" && (
            <Card padding={3}>
              <VStack gap={2}>
                <HStack justify="between" align="center">
                  <Text weight="medium">{form.script?.title || "Custom storyboard"}</Text>
                  <Badge label={`${form.script?.scenes.length ?? 0} scenes`} variant="info" />
                </HStack>
                <Text size="sm" color="secondary">
                  Edit every scene's text, visual prompt, motion, stickman action, seed, or upload custom scene clips.
                </Text>
                <HStack gap={2}>
                  <Button
                    label={form.script ? "Edit storyboard" : "Create storyboard"}
                    variant="secondary"
                    size="sm"
                    icon={<FileEdit size={14} />}
                    onClick={handleDraft}
                  />
                </HStack>
                {form.projectName && (
                  <Switch
                    label="Reuse existing visuals (fast re-assemble)"
                    value={Boolean(form.reuseVisuals)}
                    onChange={(reuseVisuals) => update({ reuseVisuals })}
                    labelPosition="start"
                    labelSpacing="spread"
                    width="100%"
                  />
                )}
              </VStack>
            </Card>
          )}

          <Featuring options={options} form={form} chosen={chosen} onChange={(featuring) => update({ featuring })} onModels={onModels} />

          <VStack gap={3}>
            <VStack gap={1}>
              <Text weight="medium">Who makes each part</Text>
              <Text size="sm" color="secondary">
                Each part has its own choice, so they can be mixed freely.
              </Text>
            </VStack>
            <Selector
              label="Script"
              options={engineChoices(options.scriptEngines)}
              value={form.scriptEngine}
              onChange={set("scriptEngine")}
              isDisabled={form.source === "preset"}
              disabledMessage="A ready-made script is already written. Choose Your topic to have one written."
            />
            <Selector
              label="Voice-over"
              options={engineChoices(options.ttsEngines)}
              value={form.ttsEngine}
              onChange={(value) => {
                if (value) update({ ttsEngine: value, voice: value === "gemini" ? options.gemini.defaultVoice : (language?.defaultVoice ?? options.defaults.voice) });
              }}
            />
            <Selector
              label="Images"
              description={form.videoEngine === "none" ? undefined : "Used only for a scene whose video clip could not be made."}
              options={engineChoices(options.imageEngines)}
              value={form.imageEngine}
              onChange={set("imageEngine")}
            />
            <Selector
              label="Video"
              description="A real-motion clip for each scene: stick figures, stock B-roll, or a video model."
              options={engineChoices(options.videoEngines)}
              value={form.videoEngine}
              onChange={set("videoEngine")}
            />
            {(!options.fastmetal.available || !options.gemini.configured) && <Button label="Set up FastMetal and Gemini" variant="ghost" size="sm" onClick={onSettings} />}
          </VStack>

          <Grid columns={2} gap={3}>
            <Selector
              label="Language"
              options={options.languages}
              value={form.language}
              onChange={(value) => {
                const next = options.languages.find((l) => l.value === value);
                if (next) update({ language: next.value, voice: form.ttsEngine === "gemini" || next.voices.includes(form.voice) ? form.voice : next.defaultVoice });
              }}
            />
            <HStack gap={1} align="end">
              <div style={{ flex: 1, minWidth: 0 }}>
                <Selector label="Voice" options={voices} value={form.voice} onChange={set("voice")} />
              </div>
              <VoicePreviewButton
                engine={form.ttsEngine}
                voice={form.voice}
                language={form.language}
                rate={form.speechRate}
                voiceStyle={form.voiceStyle}
              />
            </HStack>
          </Grid>

          {form.ttsEngine === "gemini" && (
            <TextInput
              label="Voice delivery style (optional)"
              value={form.voiceStyle || ""}
              onChange={(voiceStyle) => update({ voiceStyle })}
              placeholder="e.g. calm storyteller, whispering, energetic"
            />
          )}

          <Selector label="Art style" options={options.artStyles} value={form.artStyle} onChange={set("artStyle")} hasSearch />

          <VStack gap={2}>
            <Selector label="Subtitle style" options={options.subtitleStyles} value={form.subtitleStyle} onChange={set("subtitleStyle")} />
            <StylePreview
              styleChoice={selectedSubStyle}
              enabled={form.subtitlesEnabled !== false}
              position={form.subtitlePosition || "auto"}
              wordsPerScreen={form.wordsPerScreen ?? null}
            />
          </VStack>

          <Collapsible trigger={<Text weight="medium">More options (captions, audio, VFX, branding)</Text>} defaultIsOpen={false}>
            <VStack gap={4} paddingBlockStart={3}>
              <Grid columns={2} gap={3}>
                <Selector label="Scene transition" options={options.transitions} value={form.transition} onChange={set("transition")} />
                <Selector label="Colour grade" options={options.effects} value={form.effects} onChange={set("effects")} />
                <Selector label="VFX overlay" options={options.vfx ?? []} value={form.vfx || "none"} onChange={set("vfx")} />
                <Selector label="Quality" options={options.qualities} value={form.quality} onChange={set("quality")} />
                <Selector label="Speech speed" options={SPEECH_RATES} value={form.speechRate || "+0%"} onChange={set("speechRate")} />
                <Selector label="Background music" options={options.music.map((m) => ({ value: m, label: words(m) }))} value={form.music} onChange={set("music")} />
                <Selector
                  label="Music volume"
                  options={MUSIC_VOLUMES}
                  value={String(form.musicVolume ?? -18)}
                  onChange={(v) => v && update({ musicVolume: Number(v) })}
                />
                <Selector
                  label="Subtitle position"
                  options={options.subtitlePositions ?? []}
                  value={form.subtitlePosition || "auto"}
                  onChange={set("subtitlePosition")}
                />
                <Selector
                  label="Words per caption"
                  options={WORDS_PER_SCREEN}
                  value={form.wordsPerScreen ? String(form.wordsPerScreen) : "auto"}
                  onChange={(v) => update({ wordsPerScreen: v && v !== "auto" ? Number(v) : null })}
                />
              </Grid>

              <Grid columns={2} gap={3}>
                <TextInput
                  label="Opening hook title (2.8s)"
                  value={form.hookTitle || ""}
                  onChange={(hookTitle) => update({ hookTitle })}
                  placeholder="Optional top banner"
                />
                <TextInput
                  label="Channel watermark"
                  value={form.watermark || ""}
                  onChange={(watermark) => update({ watermark })}
                  placeholder="e.g. @yourhandle"
                />
              </Grid>

              <VStack gap={2}>
                <HStack gap={2} align="end">
                  <FileInput
                    label={musicLabel ? `Custom music: ${musicLabel}` : "Custom background music (MP3/WAV)"}
                    accept="audio/mpeg,audio/wav,audio/mp4,audio/ogg"
                    value={null}
                    onChange={(files) => uploadAudio(files, "music")}
                  />
                  {form.customMusicFile && (
                    <Button label="Clear" size="sm" variant="ghost" onClick={() => { update({ customMusicFile: null }); setMusicLabel(null); }} />
                  )}
                </HStack>
                <HStack gap={2} align="end">
                  <FileInput
                    label={voiceoverLabel ? `Custom voiceover: ${voiceoverLabel}` : "Use your own recorded voiceover (MP3/WAV/M4A)"}
                    accept="audio/mpeg,audio/wav,audio/mp4,audio/ogg"
                    value={null}
                    onChange={(files) => uploadAudio(files, "voiceover")}
                  />
                  {form.customVoiceoverFile && (
                    <Button label="Clear" size="sm" variant="ghost" onClick={() => { update({ customVoiceoverFile: null }); setVoiceoverLabel(null); }} />
                  )}
                </HStack>
              </VStack>

              <Switch
                label="Burn subtitles into video"
                value={form.subtitlesEnabled !== false}
                onChange={(subtitlesEnabled) => update({ subtitlesEnabled })}
                labelPosition="start"
                labelSpacing="spread"
                width="100%"
              />
              <Switch
                label="Transition whoosh sound effects"
                value={form.whoosh === true || (form.whoosh === null && (form.music !== "none" || Boolean(form.customMusicFile)))}
                onChange={(whoosh) => update({ whoosh })}
                labelPosition="start"
                labelSpacing="spread"
                width="100%"
              />
              <Switch
                label="Progress bar along the bottom"
                value={form.progressBar}
                onChange={(progressBar) => update({ progressBar })}
                labelPosition="start"
                labelSpacing="spread"
                width="100%"
              />
            </VStack>
          </Collapsible>

          <Button
            label={busy ? "Add to render queue" : form.reuseVisuals ? "Fast re-assemble short" : "Make the short"}
            type="submit"
            variant="primary"
            icon={busy ? <ListPlus size={16} /> : <Sparkles size={16} />}
            width="100%"
            isDisabled={!ready}
            isLoading={start.isPending}
          />
        </VStack>
      </form>

      {editorOpen && (
        <StoryboardDialog
          options={options}
          models={known}
          defaultVideoEngine={form.videoEngine}
          initialScript={form.script ?? BLANK_SCRIPT}
          hasSavedProject={Boolean(form.projectName)}
          initialRegenerateScenes={form.regenerateScenes ?? []}
          onClose={() => setEditorOpen(false)}
          onSave={(script, regenerateScenes) => {
            update({ source: "custom", script, regenerateScenes });
            setEditorOpen(false);
            toast.ok("Storyboard saved");
          }}
          onRenderNow={(script, regenerateScenes, reuseVisuals) => {
            const next: RenderRequest = {
              ...form,
              source: "custom",
              script,
              regenerateScenes,
              reuseVisuals,
              featuring: chosen,
            };
            update({ source: "custom", script, regenerateScenes, reuseVisuals });
            setEditorOpen(false);
            const check = renderRequest.safeParse(next);
            if (check.success) start.mutate(check.data);
          }}
        />
      )}
    </Card>
  );
}

/** The form appears once the Python tool has said what can be chosen. */
export function GenerateForm({ options, ...rest }: Omit<Props, "options"> & { options: Options | undefined }) {
  return options ? <Form options={options} {...rest} /> : <Skeleton height={FORM_HEIGHT} />;
}
