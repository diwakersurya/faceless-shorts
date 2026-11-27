import { AppShell } from "@astryxdesign/core/AppShell";
import { Banner } from "@astryxdesign/core/Banner";
import { IconButton } from "@astryxdesign/core/IconButton";
import { TopNav, TopNavHeading } from "@astryxdesign/core/TopNav";
import { VStack } from "@astryxdesign/core/VStack";
import * as stylex from "@stylexjs/stylex";
import { Clapperboard, Settings, Users } from "lucide-react";
import { useState } from "react";
import { GenerateForm } from "./components/GenerateForm";
import { HStack } from "@astryxdesign/core/HStack";
import { Library } from "./components/Library";
import { ModelsDialog } from "./components/ModelsDialog";
import { RenderProgress } from "./components/RenderProgress";
import { SettingsDialog } from "./components/SettingsDialog";
import { ToastBridge } from "./components/ToastBridge";
import { VideoDialog } from "./components/VideoDialog";
import { useJob, useOptions, useVideos } from "./queries";
import { useRoute } from "./route";

const NARROW = "@media (max-width: 900px)";

const s = stylex.create({
  // The form on the left, what it makes on the right. On a narrow screen they stack.
  page: {
    display: "grid",
    gridTemplateColumns: { default: "400px minmax(0, 1fr)", [NARROW]: "minmax(0, 1fr)" },
    gap: "var(--spacing-5)",
    alignItems: "start",
    maxWidth: 1280,
    marginInline: "auto",
  },
});

export function App() {
  const route = useRoute();
  const options = useOptions();
  const job = useJob().data?.job ?? null;
  const videos = useVideos().data?.videos;
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modelsOpen, setModelsOpen] = useState(false);
  return (
    <AppShell
      height="fill"
      contentPadding={5}
      topNav={
        <TopNav
          heading={<TopNavHeading heading="Faceless Shorts" logo={<Clapperboard size={20} />} />}
          endContent={
            <HStack gap={1}>
              <IconButton label="Your models" tooltip="Your models" icon={<Users size={18} />} variant="ghost" isDisabled={!options.data} onClick={() => setModelsOpen(true)} />
              <IconButton label="Settings" tooltip="Settings" icon={<Settings size={18} />} variant="ghost" isDisabled={!options.data} onClick={() => setSettingsOpen(true)} />
            </HStack>
          }
        />
      }
      banner={options.isError ? <Banner status="error" container="section" title="The app is not ready to make shorts" description={options.error.message} /> : undefined}
    >
      <div {...stylex.props(s.page)}>
        <GenerateForm options={options.data} busy={job?.state === "running"} onSettings={() => setSettingsOpen(true)} onModels={() => setModelsOpen(true)} />
        <VStack gap={5}>
          {job && <RenderProgress job={job} />}
          <Library videos={videos} />
        </VStack>
      </div>
      {route.video && <VideoDialog name={route.video} videos={videos} />}
      {settingsOpen && options.data && <SettingsDialog options={options.data} onClose={() => setSettingsOpen(false)} />}
      {modelsOpen && options.data && <ModelsDialog options={options.data} onClose={() => setModelsOpen(false)} />}
      <ToastBridge />
    </AppShell>
  );
}
