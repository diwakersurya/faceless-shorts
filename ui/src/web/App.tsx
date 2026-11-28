import { AppShell } from "@astryxdesign/core/AppShell";
import { Banner } from "@astryxdesign/core/Banner";
import { HStack } from "@astryxdesign/core/HStack";
import { IconButton } from "@astryxdesign/core/IconButton";
import { TopNav, TopNavHeading } from "@astryxdesign/core/TopNav";
import { VStack } from "@astryxdesign/core/VStack";
import * as stylex from "@stylexjs/stylex";
import { Clapperboard, Settings, Users } from "lucide-react";
import { useState } from "react";
import type { SavedProject } from "./api";
import { GenerateForm } from "./components/GenerateForm";
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
  page: {
    display: "grid",
    gridTemplateColumns: { default: "420px minmax(0, 1fr)", [NARROW]: "minmax(0, 1fr)" },
    gap: "var(--spacing-5)",
    alignItems: "start",
    maxWidth: 1280,
    marginInline: "auto",
  },
});

export function App() {
  const route = useRoute();
  const options = useOptions();
  const jobData = useJob().data;
  const job = jobData?.job ?? null;
  const queue = jobData?.queue ?? [];
  const videos = useVideos().data?.videos;
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modelsOpen, setModelsOpen] = useState(false);
  const [loaded, setLoaded] = useState<{ version: number; project: SavedProject } | null>(null);

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
        <GenerateForm
          key={loaded?.version ?? 0}
          options={options.data}
          busy={job?.state === "running"}
          loadedProject={loaded?.project ?? null}
          onClearProject={() => setLoaded(null)}
          onSettings={() => setSettingsOpen(true)}
          onModels={() => setModelsOpen(true)}
        />
        <VStack gap={5}>
          {job && <RenderProgress job={job} queue={queue} />}
          <Library videos={videos} />
        </VStack>
      </div>
      {route.video && (
        <VideoDialog
          name={route.video}
          videos={videos}
          onEditProject={(project) => setLoaded({ version: Date.now(), project })}
        />
      )}
      {settingsOpen && options.data && <SettingsDialog options={options.data} onClose={() => setSettingsOpen(false)} />}
      {modelsOpen && options.data && <ModelsDialog options={options.data} onClose={() => setModelsOpen(false)} />}
      <ToastBridge />
    </AppShell>
  );
}
