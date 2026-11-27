import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { Collapsible } from "@astryxdesign/core/Collapsible";
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
import { Sparkles, UserPlus } from "lucide-react";
import { useState } from "react";
import { type RenderRequest, renderRequest } from "../../shared/schema";
import type { Choice, Engine, MyModel, Options } from "../api";
import { useMyModels, useStartRender } from "../queries";

const STORE = "faceless-shorts-ui.form";
const FORM_HEIGHT = 560; // the loading placeholder takes the room the form will take

/** What the form starts with: the tool's defaults, then what was chosen last time where that still exists. */
function initial(options: Options): RenderRequest {
  let last: Partial<Record<keyof RenderRequest, unknown>> = {};
  try {
    last = JSON.parse(localStorage.getItem(STORE) ?? "{}") ?? {};
  } catch {} // nothing usable was kept
  const d = options.defaults;
  const pick = (value: unknown, list: Choice[], fallback: string) => (list.some((c) => c.value === value) ? (value as string) : fallback);
  // An engine that was installed, running or had its key last time may not be usable now.
  const usable = (value: unknown, list: Engine[], fallback: string) => pick(value, list.filter((e) => e.available), fallback);
  const language = pick(last.language, options.languages, d.language);
  const spoken = options.languages.find((l) => l.value === language);
  const ttsEngine = usable(last.ttsEngine, options.ttsEngines, d.ttsEngine);
  // Each speech engine has its own voices: a voice kept from last time counts only if the engine has it.
  const voice =
    ttsEngine === "gemini"
      ? pick(last.voice, options.gemini.voices, options.gemini.defaultVoice)
      : spoken?.voices.includes(last.voice as string)
        ? (last.voice as string)
        : (spoken?.defaultVoice ?? d.voice);
  return {
    source: last.source === "preset" ? "preset" : "topic",
    topic: typeof last.topic === "string" ? last.topic : "",
    preset: pick(last.preset, options.presets, options.presets[0]?.value ?? ""),
    language,
    voice,
    subtitleStyle: pick(last.subtitleStyle, options.subtitleStyles, d.subtitleStyle),
    artStyle: pick(last.artStyle, options.artStyles, d.artStyle),
    transition: pick(last.transition, options.transitions, d.transition),
    effects: pick(last.effects, options.effects, d.effects),
    quality: pick(last.quality, options.qualities, d.quality),
    music: options.music.includes(last.music as string) ? (last.music as string) : d.music,
    scriptEngine: usable(last.scriptEngine, options.scriptEngines, d.scriptEngine),
    ttsEngine,
    imageEngine: usable(last.imageEngine, options.imageEngines, d.imageEngine),
    videoEngine: usable(last.videoEngine, options.videoEngines, d.videoEngine),
    // Checked against your models when the form is shown: one may have been deleted since.
    featuring: Array.isArray(last.featuring) ? last.featuring.filter((id): id is string => typeof id === "string") : [],
    progressBar: typeof last.progressBar === "boolean" ? last.progressBar : true,
  };
}

/** An engine that cannot be used here stays in the list, switched off, with what it needs. */
const engineChoices = (list: Engine[]) => list.map((e) => ({ value: e.value, label: e.label, description: e.description ?? undefined, disabled: !e.available }));

const words = (key: string) => key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

/** How the chosen engines will show your models: from their photos, or only from their descriptions. */
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

/** Your models to put in every scene, or the way to make the first one. */
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

type Props = { options: Options; busy: boolean; onSettings: () => void; onModels: () => void };

function Form({ options, busy, onSettings, onModels }: Props) {
  const start = useStartRender();
  const known = useMyModels().data?.models;
  const [form, setForm] = useState(() => initial(options));
  const update = (patch: Partial<RenderRequest>) => {
    const next = { ...form, ...patch };
    setForm(next);
    localStorage.setItem(STORE, JSON.stringify(next)); // the choices are still there after a reload
  };
  // A model kept from last time that has been deleted since is left out, not sent.
  const chosen = known ? form.featuring.filter((id) => known.some((m) => m.id === id)) : [];
  const parsed = renderRequest.safeParse({ ...form, featuring: chosen });
  // An empty topic is not shown as an error; the button simply stays off until there is one.
  const topicError = form.source === "topic" && form.topic.trim() && !parsed.success ? parsed.error.issues.find((i) => i.path[0] === "topic")?.message : undefined;
  const ready = parsed.success && !busy && !start.isPending;
  const language = options.languages.find((l) => l.value === form.language);
  const voices = form.ttsEngine === "gemini" ? options.gemini.voices : options.voices.filter((v) => language?.voices.includes(v.value));
  const set = (field: keyof RenderRequest) => (value: string | null) => {
    if (value !== null) update({ [field]: value });
  };
  return (
    <Card>
      <form
        action={() => {
          if (ready) start.mutate(parsed.data);
        }}
      >
        <VStack gap={4}>
          <Heading level={2}>New short</Heading>
          <SegmentedControl label="What the short is about" layout="fill" value={form.source} onChange={(source) => update({ source: source as RenderRequest["source"] })}>
            <SegmentedControlItem value="topic" label="Your topic" />
            <SegmentedControlItem value="preset" label="Ready-made script" />
          </SegmentedControl>
          {form.source === "topic" ? (
            <TextInput
              label="Topic"
              value={form.topic}
              onChange={(topic) => update({ topic })}
              placeholder="Why foxes hunt at dawn"
              description="A script is written for it. Any language from the list below works."
              autoComplete="off"
              status={topicError ? { type: "error", message: topicError } : undefined}
            />
          ) : (
            <Selector label="Ready-made script" options={options.presets.map((p) => ({ value: p.value, label: p.label, description: `${p.scenes} scenes` }))} value={form.preset} onChange={set("preset")} />
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
                // The voices belong to the engine: changing it moves to that engine's usual voice.
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
              description="A real-motion clip for each scene, instead of camera moves on its image: stick figures drawn by code, or a video model."
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
                // Each language has its own voices: keep the voice if it speaks the new language, else take that language's usual one.
                // Gemini's voices speak every language, so they stay.
                if (next) update({ language: next.value, voice: form.ttsEngine === "gemini" || next.voices.includes(form.voice) ? form.voice : next.defaultVoice });
              }}
            />
            <Selector label="Voice" options={voices} value={form.voice} onChange={set("voice")} />
          </Grid>
          <Selector label="Art style" options={options.artStyles} value={form.artStyle} onChange={set("artStyle")} hasSearch />
          <Selector label="Subtitle style" options={options.subtitleStyles} value={form.subtitleStyle} onChange={set("subtitleStyle")} />
          <Collapsible trigger={<Text weight="medium">More options</Text>} defaultIsOpen={false}>
            <VStack gap={4} paddingBlockStart={3}>
              <Grid columns={2} gap={3}>
                <Selector label="Scene transition" options={options.transitions} value={form.transition} onChange={set("transition")} />
                <Selector label="Colour grade" options={options.effects} value={form.effects} onChange={set("effects")} />
                <Selector label="Quality" options={options.qualities} value={form.quality} onChange={set("quality")} />
                <Selector label="Background music" options={options.music.map((m) => ({ value: m, label: words(m) }))} value={form.music} onChange={set("music")} />
              </Grid>
              <Switch label="Progress bar along the bottom" value={form.progressBar} onChange={(progressBar) => update({ progressBar })} labelPosition="start" labelSpacing="spread" width="100%" />
            </VStack>
          </Collapsible>
          <Button
            label="Make the short"
            type="submit"
            variant="primary"
            icon={<Sparkles size={16} />}
            width="100%"
            isDisabled={!ready}
            isLoading={start.isPending}
            tooltip={busy ? "A short is being made already. Wait for it or stop it first." : undefined}
          />
        </VStack>
      </form>
    </Card>
  );
}

/** The form appears once the Python tool has said what can be chosen. */
export function GenerateForm({ options, ...rest }: Omit<Props, "options"> & { options: Options | undefined }) {
  return options ? <Form options={options} {...rest} /> : <Skeleton height={FORM_HEIGHT} />;
}
