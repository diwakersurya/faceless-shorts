import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { Collapsible } from "@astryxdesign/core/Collapsible";
import { Grid } from "@astryxdesign/core/Grid";
import { Heading } from "@astryxdesign/core/Heading";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
import { Selector } from "@astryxdesign/core/Selector";
import { Skeleton } from "@astryxdesign/core/Skeleton";
import { Switch } from "@astryxdesign/core/Switch";
import { Text } from "@astryxdesign/core/Text";
import { TextInput } from "@astryxdesign/core/TextInput";
import { VStack } from "@astryxdesign/core/VStack";
import { Sparkles } from "lucide-react";
import { useState } from "react";
import { type RenderRequest, renderRequest } from "../../shared/schema";
import type { Choice, Options } from "../api";
import { useStartRender } from "../queries";

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
  const language = pick(last.language, options.languages, d.language);
  const spoken = options.languages.find((l) => l.value === language);
  const videoEngine = pick(last.videoEngine, options.videoEngines, d.videoEngine);
  return {
    source: last.source === "preset" ? "preset" : "topic",
    topic: typeof last.topic === "string" ? last.topic : "",
    preset: pick(last.preset, options.presets, options.presets[0]?.value ?? ""),
    language,
    voice: spoken?.voices.includes(last.voice as string) ? (last.voice as string) : (spoken?.defaultVoice ?? d.voice),
    subtitleStyle: pick(last.subtitleStyle, options.subtitleStyles, d.subtitleStyle),
    artStyle: pick(last.artStyle, options.artStyles, d.artStyle),
    transition: pick(last.transition, options.transitions, d.transition),
    effects: pick(last.effects, options.effects, d.effects),
    quality: pick(last.quality, options.qualities, d.quality),
    music: options.music.includes(last.music as string) ? (last.music as string) : d.music,
    imageEngine: pick(last.imageEngine, options.imageEngines, d.imageEngine),
    // The video model may have been removed since it was last chosen.
    videoEngine: videoEngine === "fastmetal" && !options.fastmetal.available ? d.videoEngine : videoEngine,
    progressBar: typeof last.progressBar === "boolean" ? last.progressBar : true,
  };
}

const words = (key: string) => key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

type Props = { options: Options; busy: boolean; onSettings: () => void };

function Form({ options, busy, onSettings }: Props) {
  const start = useStartRender();
  const [form, setForm] = useState(() => initial(options));
  const update = (patch: Partial<RenderRequest>) => {
    const next = { ...form, ...patch };
    setForm(next);
    localStorage.setItem(STORE, JSON.stringify(next)); // the choices are still there after a reload
  };
  const parsed = renderRequest.safeParse(form);
  // An empty topic is not shown as an error; the button simply stays off until there is one.
  const topicError = form.source === "topic" && form.topic.trim() && !parsed.success ? parsed.error.issues.find((i) => i.path[0] === "topic")?.message : undefined;
  const ready = parsed.success && !busy && !start.isPending;
  const language = options.languages.find((l) => l.value === form.language);
  const voices = options.voices.filter((v) => language?.voices.includes(v.value));
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
            <Selector label="Script" options={options.presets.map((p) => ({ value: p.value, label: p.label, description: `${p.scenes} scenes` }))} value={form.preset} onChange={set("preset")} />
          )}
          <Switch
            label="Generate scenes as AI video"
            description="A real-motion clip for each scene from the local FastMetal model, instead of camera moves on a still image. About 5 minutes per scene."
            value={form.videoEngine === "fastmetal"}
            onChange={(on) => update({ videoEngine: on ? "fastmetal" : "none" })}
            isDisabled={!options.fastmetal.available}
            disabledMessage={`FastMetal was not found: ${options.fastmetal.missing} is missing. Set its folder in Settings.`}
            labelPosition="start"
            labelSpacing="spread"
            width="100%"
          />
          {!options.fastmetal.available && <Button label="Set the FastMetal folder" variant="ghost" size="sm" onClick={onSettings} />}
          <Grid columns={2} gap={3}>
            <Selector
              label="Language"
              options={options.languages}
              value={form.language}
              onChange={(value) => {
                const next = options.languages.find((l) => l.value === value);
                // Each language has its own voices: keep the voice if it speaks the new language, else take that language's usual one.
                if (next) update({ language: next.value, voice: next.voices.includes(form.voice) ? form.voice : next.defaultVoice });
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
              <Selector label="Still images come from" description="Used for every scene that is not an AI video clip." options={options.imageEngines} value={form.imageEngine} onChange={set("imageEngine")} />
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
