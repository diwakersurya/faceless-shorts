import { Button } from "@astryxdesign/core/Button";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { HStack } from "@astryxdesign/core/HStack";
import { InputGroup } from "@astryxdesign/core/InputGroup";
import { TextInput } from "@astryxdesign/core/TextInput";
import { VStack } from "@astryxdesign/core/VStack";
import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import { settings } from "../../shared/schema";
import type { Options } from "../api";
import { useSaveSettings } from "../queries";

/** What this computer needs for the AI engines: where the local video model lives, and the key for Google's online models. The dialog stays open after saving, to show what was found. */
export function SettingsDialog({ options, onClose }: { options: Options; onClose: () => void }) {
  const save = useSaveSettings();
  const [value, setValue] = useState(options.settings.fastmetalHome);
  const [key, setKey] = useState(""); // a saved key is never sent to the page, so the field starts empty
  const [keyShown, setKeyShown] = useState(false);
  const parsed = settings.safeParse({ fastmetalHome: value, ...(key.trim() ? { geminiKey: key } : {}) });
  const problem = (field: string) => (parsed.success ? undefined : parsed.error.issues.find((i) => i.path[0] === field)?.message);
  const changed = value.trim() !== options.settings.fastmetalHome;
  const ready = parsed.success && (changed || key.trim() !== "") && !save.isPending;
  const { available, home, missing, fix } = options.fastmetal;
  const status = problem("fastmetalHome")
    ? ({ type: "error", message: problem("fastmetalHome") } as const)
    : changed
      ? undefined // not checked yet: the answer comes after saving
      : available
        ? ({ type: "success", message: `FastMetal found in ${home}` } as const)
        : // Says exactly what is not there and the command that puts it there: a missing folder, code, environment or model.
          ({ type: "warning", message: `Not ready: ${missing} does not exist. ${fix}` } as const);
  const { configured, source, ending } = options.gemini;
  const keyStatus = problem("geminiKey")
    ? ({ type: "error", message: problem("geminiKey") } as const)
    : key.trim() || !configured
      ? undefined // a new key is checked with Google when it is saved
      : ({ type: "success", message: source === "settings" ? `A key ending in ${ending} is saved.` : `Using the key ending in ${ending} from ${source}, set where this app was started.` } as const);
  const close = (open: boolean) => {
    if (!open) onClose();
  };
  return (
    <Dialog isOpen onOpenChange={close} purpose="form" width={520}>
      <DialogHeader title="Settings" onOpenChange={close} />
      <form
        action={() => {
          if (ready) save.mutate(parsed.data, { onSuccess: () => setKey("") });
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
            description="The folder on the computer that runs this app, holding FastVideo/ and FastMetal-1.3B-QAD/. It makes the AI video scenes. Leave it empty to use ~/fastmetal-video."
            status={status}
          />
          <InputGroup
            label="Gemini API key"
            description="From aistudio.google.com/apikey. Gemini then writes scripts (free) and makes images and video (paid: the key needs billing set up). The key stays on the computer that runs this app."
            status={keyStatus}
          >
            <TextInput
              label="Key"
              isLabelHidden
              type={keyShown ? "text" : "password"}
              value={key}
              onChange={setKey}
              autoComplete="off"
              hasSpellCheck={false}
              placeholder={configured ? "Paste a new key to replace it" : "Paste the key"}
              width="100%"
            />
            <Button label={keyShown ? "Hide" : "Show"} icon={keyShown ? <EyeOff size={16} /> : <Eye size={16} />} variant="secondary" onClick={() => setKeyShown(!keyShown)} />
          </InputGroup>
          {source === "settings" && (
            <HStack>
              <Button label="Forget the saved key" variant="ghost" size="sm" isDisabled={save.isPending} onClick={() => save.mutate({ fastmetalHome: options.settings.fastmetalHome, geminiKey: "" })} />
            </HStack>
          )}
          <HStack justify="end" gap={2}>
            <Button label="Close" variant="secondary" onClick={onClose} />
            <Button label="Save" type="submit" variant="primary" isDisabled={!ready} isLoading={save.isPending} />
          </HStack>
        </VStack>
      </form>
    </Dialog>
  );
}
