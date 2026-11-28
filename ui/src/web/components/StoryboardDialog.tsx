import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { Collapsible } from "@astryxdesign/core/Collapsible";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { FileInput } from "@astryxdesign/core/FileInput";
import { Grid } from "@astryxdesign/core/Grid";
import { HStack } from "@astryxdesign/core/HStack";
import { IconButton } from "@astryxdesign/core/IconButton";
import { MultiSelector } from "@astryxdesign/core/MultiSelector";
import { Selector } from "@astryxdesign/core/Selector";
import { Switch } from "@astryxdesign/core/Switch";
import { Text } from "@astryxdesign/core/Text";
import { TextArea } from "@astryxdesign/core/TextArea";
import { TextInput } from "@astryxdesign/core/TextInput";
import { VStack } from "@astryxdesign/core/VStack";
import { ArrowDown, ArrowUp, Plus, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import type { DraftScript, SceneDraft } from "../../shared/schema";
import type { MyModel, Options } from "../api";
import { useUploadFile } from "../queries";
import { toast } from "../toast";

type Props = {
  options: Options;
  models: MyModel[];
  defaultVideoEngine: string;
  initialScript: DraftScript;
  hasSavedProject: boolean;
  initialRegenerateScenes: number[];
  onSave: (script: DraftScript, regenerateScenes: number[]) => void;
  onRenderNow: (script: DraftScript, regenerateScenes: number[], reuseVisuals: boolean) => void;
  onClose: () => void;
};

function reindex(scenes: SceneDraft[]): SceneDraft[] {
  return scenes.map((sc, i) => ({ ...sc, index: i + 1 }));
}

export function StoryboardDialog({
  options,
  models,
  defaultVideoEngine,
  initialScript,
  hasSavedProject,
  initialRegenerateScenes,
  onSave,
  onRenderNow,
  onClose,
}: Props) {
  const upload = useUploadFile();
  const [script, setScript] = useState<DraftScript>(() => ({
    ...initialScript,
    scenes: reindex(initialScript.scenes),
  }));
  const [regen, setRegen] = useState<number[]>(initialRegenerateScenes);

  const updateScene = (idx: number, patch: Partial<SceneDraft>) => {
    setScript((prev) => ({
      ...prev,
      scenes: prev.scenes.map((sc, i) => (i === idx ? { ...sc, ...patch } : sc)),
    }));
  };

  const moveScene = (idx: number, dir: -1 | 1) => {
    const target = idx + dir;
    if (target < 0 || target >= script.scenes.length) return;
    setScript((prev) => {
      const next = [...prev.scenes];
      [next[idx], next[target]] = [next[target]!, next[idx]!];
      return { ...prev, scenes: reindex(next) };
    });
  };

  const removeScene = (idx: number) => {
    if (script.scenes.length <= 1) return;
    setScript((prev) => ({
      ...prev,
      scenes: reindex(prev.scenes.filter((_, i) => i !== idx)),
    }));
  };

  const addScene = () => {
    setScript((prev) => ({
      ...prev,
      scenes: reindex([
        ...prev.scenes,
        {
          index: prev.scenes.length + 1,
          text: "",
          visual_prompt: "",
          motion: "punch_zoom_in",
        },
      ]),
    }));
  };

  const toggleRegen = (sceneNum: number) => {
    setRegen((prev) => (prev.includes(sceneNum) ? prev.filter((n) => n !== sceneNum) : [...prev, sceneNum]));
  };

  const uploadSceneMedia = async (idx: number, picked: File | File[] | null) => {
    const file = Array.isArray(picked) ? picked[0] : picked;
    if (!file) return;
    try {
      const res = await upload.mutateAsync(file);
      updateScene(idx, { media_path: res.file });
      toast.ok(`Attached ${res.name} to Scene ${idx + 1}`);
    } catch {}
  };

  const valid = script.title.trim().length > 0 && script.scenes.length > 0 && script.scenes.every((s) => s.text.trim().length > 0);
  const videoEngineOptions = [
    { value: "__inherit__", label: `Default (${defaultVideoEngine})` },
    ...options.videoEngines.map((e) => ({ value: e.value, label: e.label, disabled: !e.available })),
  ];

  return (
    <Dialog isOpen onOpenChange={(open) => !open && onClose()} purpose="info" width={920} maxHeight="90dvh">
      <DialogHeader title="Script & Storyboard Editor" onOpenChange={(open) => !open && onClose()} />
      <VStack gap={4} padding={4}>
        <TextInput
          label="Title"
          value={script.title}
          onChange={(title) => setScript((prev) => ({ ...prev, title }))}
          placeholder="Short title"
        />
        <VStack gap={3}>
          {script.scenes.map((scene, idx) => {
            const sceneNum = idx + 1;
            const effectiveVe = scene.video_engine || defaultVideoEngine;
            const isStickman = effectiveVe === "stickman";
            const markedRegen = regen.includes(sceneNum);
            return (
              <Card key={idx} padding={3}>
                <VStack gap={3}>
                  <HStack justify="between" align="center" gap={2}>
                    <HStack gap={2} align="center">
                      <Badge label={`Scene ${sceneNum}`} variant="info" />
                      {scene.media_path && <Badge label="Custom media" variant="success" />}
                      {hasSavedProject && markedRegen && <Badge label="Will regenerate" variant="warning" />}
                    </HStack>
                    <HStack gap={1}>
                      {hasSavedProject && (
                        <Button
                          label={markedRegen ? "Regenerating" : "Regenerate scene"}
                          size="sm"
                          variant={markedRegen ? "primary" : "ghost"}
                          icon={<RefreshCw size={14} />}
                          onClick={() => toggleRegen(sceneNum)}
                        />
                      )}
                      <IconButton label="Move up" icon={<ArrowUp size={14} />} variant="ghost" size="sm" isDisabled={idx === 0} onClick={() => moveScene(idx, -1)} />
                      <IconButton label="Move down" icon={<ArrowDown size={14} />} variant="ghost" size="sm" isDisabled={idx === script.scenes.length - 1} onClick={() => moveScene(idx, 1)} />
                      <IconButton label="Remove scene" icon={<Trash2 size={14} />} variant="ghost" size="sm" isDisabled={script.scenes.length <= 1} onClick={() => removeScene(idx)} />
                    </HStack>
                  </HStack>

                  <TextArea
                    label="Spoken text"
                    value={scene.text}
                    onChange={(text) => updateScene(idx, { text })}
                    placeholder="What the narrator says in this scene..."
                    rows={2}
                  />
                  <TextArea
                    label="Visual prompt"
                    value={scene.visual_prompt}
                    onChange={(visual_prompt) => updateScene(idx, { visual_prompt })}
                    placeholder="Describe what should appear on screen..."
                    rows={2}
                  />

                  <Grid columns={2} gap={3}>
                    <Selector
                      label="Camera motion"
                      options={options.motions}
                      value={scene.motion || "punch_zoom_in"}
                      onChange={(motion) => motion && updateScene(idx, { motion })}
                    />
                    <Selector
                      label="Scene video engine"
                      options={videoEngineOptions}
                      value={scene.video_engine || "__inherit__"}
                      onChange={(val) => updateScene(idx, { video_engine: val && val !== "__inherit__" ? val : null })}
                    />
                  </Grid>

                  {isStickman && (
                    <Grid columns={2} gap={3}>
                      <Selector
                        label="Stickman action"
                        options={[{ value: "__auto__", label: "Auto from text" }, ...options.stickActions]}
                        value={scene.stick?.action || "__auto__"}
                        onChange={(val) =>
                          updateScene(idx, {
                            stick: { ...(scene.stick ?? {}), action: val && val !== "__auto__" ? val : undefined },
                          })
                        }
                      />
                      <Selector
                        label="Main character"
                        options={[{ value: "__auto__", label: "Auto (Classic / model)" }, ...(options.stickCharacters ?? [])]}
                        value={scene.stick?.character || "__auto__"}
                        onChange={(val) =>
                          updateScene(idx, {
                            stick: { ...(scene.stick ?? {}), character: val && val !== "__auto__" ? val : undefined },
                          })
                        }
                      />
                      <Selector
                        label="Second figure"
                        options={[{ value: "__none__", label: "None" }, ...options.stickActions]}
                        value={scene.stick?.other || "__none__"}
                        onChange={(val) =>
                          updateScene(idx, {
                            stick: { ...(scene.stick ?? {}), other: val && val !== "__none__" ? val : undefined },
                          })
                        }
                      />
                      <Selector
                        label="Second character"
                        options={[{ value: "__auto__", label: "Auto (Classic / model)" }, ...(options.stickCharacters ?? [])]}
                        value={scene.stick?.other_character || "__auto__"}
                        onChange={(val) =>
                          updateScene(idx, {
                            stick: { ...(scene.stick ?? {}), other_character: val && val !== "__auto__" ? val : undefined },
                          })
                        }
                      />
                      <TextInput
                        label="Speech bubble"
                        value={scene.stick?.bubble || ""}
                        onChange={(bubble) => updateScene(idx, { stick: { ...(scene.stick ?? {}), bubble: bubble || undefined } })}
                        placeholder="e.g. ?, !, NO"
                      />
                      <TextInput
                        label="Top headline"
                        value={scene.stick?.headline || ""}
                        onChange={(headline) => updateScene(idx, { stick: { ...(scene.stick ?? {}), headline: headline || undefined } })}
                        placeholder="Short 2-5 word headline"
                      />
                    </Grid>
                  )}

                  <Collapsible trigger={<Text size="sm" weight="medium">Scene media, seed &amp; models</Text>} defaultIsOpen={Boolean(scene.media_path || scene.seed != null || scene.featuring?.length)}>
                    <VStack gap={3} paddingBlockStart={2}>
                      <Grid columns={2} gap={3}>
                        <TextInput
                          label="Visual seed (optional)"
                          value={scene.seed != null ? String(scene.seed) : ""}
                          onChange={(raw) => {
                            const digits = raw.replace(/\D/g, "");
                            updateScene(idx, { seed: digits ? Number(digits) : null });
                          }}
                          placeholder="Random"
                        />
                        {models.length > 0 && (
                          <MultiSelector
                            label="Models in this scene"
                            isOptional
                            options={models.map((m) => ({ value: m.id, label: m.name }))}
                            value={scene.featuring ?? []}
                            onChange={(featuring) => updateScene(idx, { featuring: featuring.length ? featuring : null })}
                            placeholder="Use short's Featuring"
                            triggerDisplay="badges"
                            hasClear
                          />
                        )}
                      </Grid>
                      <HStack gap={2} align="end">
                        <FileInput
                          label="Custom image or video clip for this scene"
                          accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm"
                          value={null}
                          onChange={(files) => uploadSceneMedia(idx, files)}
                        />
                        {scene.media_path && (
                          <Button label="Clear media" size="sm" variant="ghost" onClick={() => updateScene(idx, { media_path: null })} />
                        )}
                      </HStack>
                    </VStack>
                  </Collapsible>
                </VStack>
              </Card>
            );
          })}
        </VStack>

        <HStack justify="between" align="center" wrap="wrap" gap={2}>
          <Button label="Add scene" variant="secondary" size="sm" icon={<Plus size={14} />} onClick={addScene} />
          <HStack gap={2} wrap="wrap">
            <Button label="Cancel" variant="ghost" onClick={onClose} />
            <Button label="Save storyboard" variant="secondary" isDisabled={!valid} onClick={() => onSave(script, regen)} />
            {hasSavedProject && (
              <Button
                label={regen.length ? `Re-render (${regen.length} regen)` : "Fast re-assemble"}
                variant="primary"
                icon={<RefreshCw size={14} />}
                isDisabled={!valid}
                onClick={() => onRenderNow(script, regen, true)}
              />
            )}
            <Button
              label="Render full short"
              variant={hasSavedProject ? "secondary" : "primary"}
              icon={<Sparkles size={14} />}
              isDisabled={!valid}
              onClick={() => onRenderNow(script, [], false)}
            />
          </HStack>
        </HStack>
      </VStack>
    </Dialog>
  );
}
