import { Button } from "@astryxdesign/core/Button";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { HStack } from "@astryxdesign/core/HStack";
import { TextInput } from "@astryxdesign/core/TextInput";
import { VStack } from "@astryxdesign/core/VStack";
import { useState } from "react";
import { settings } from "../../shared/schema";
import type { Options } from "../api";
import { useSaveSettings } from "../queries";

/** Where the local video model lives. The dialog stays open after saving, to show whether it was found there. */
export function SettingsDialog({ options, onClose }: { options: Options; onClose: () => void }) {
  const save = useSaveSettings();
  const [value, setValue] = useState(options.settings.fastmetalHome);
  const parsed = settings.safeParse({ fastmetalHome: value });
  const changed = value.trim() !== options.settings.fastmetalHome;
  const ready = parsed.success && changed && !save.isPending;
  const { available, home, missing } = options.fastmetal;
  const status = !parsed.success
    ? ({ type: "error", message: parsed.error.issues[0]!.message } as const)
    : changed
      ? undefined // not checked yet: the answer comes after saving
      : available
        ? ({ type: "success", message: `FastMetal found in ${home}` } as const)
        : ({ type: "warning", message: `FastMetal not found: ${missing} is missing. The README has the install steps.` } as const);
  const close = (open: boolean) => {
    if (!open) onClose();
  };
  return (
    <Dialog isOpen onOpenChange={close} purpose="form" width={520}>
      <DialogHeader title="Settings" onOpenChange={close} />
      <form
        action={() => {
          if (ready) save.mutate(parsed.data);
        }}
      >
        <VStack gap={4} paddingInline={4} paddingBlockEnd={4}>
          <TextInput
            label="FastMetal folder"
            value={value}
            onChange={setValue}
            hasAutoFocus
            autoComplete="off"
            placeholder="~/fastmetal-video"
            description="The folder that holds FastVideo/ and FastMetal-1.3B-QAD/. It makes the AI video scenes. Leave it empty to use ~/fastmetal-video."
            status={status}
          />
          <HStack justify="end" gap={2}>
            <Button label="Close" variant="secondary" onClick={onClose} />
            <Button label="Save" type="submit" variant="primary" isDisabled={!ready} isLoading={save.isPending} />
          </HStack>
        </VStack>
      </form>
    </Dialog>
  );
}
