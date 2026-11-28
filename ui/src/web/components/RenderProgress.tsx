import { AlertDialog } from "@astryxdesign/core/AlertDialog";
import { Badge } from "@astryxdesign/core/Badge";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { Collapsible } from "@astryxdesign/core/Collapsible";
import { HStack } from "@astryxdesign/core/HStack";
import { Heading } from "@astryxdesign/core/Heading";
import { IconButton } from "@astryxdesign/core/IconButton";
import { ProgressBar } from "@astryxdesign/core/ProgressBar";
import { Step, Stepper } from "@astryxdesign/core/Stepper";
import { Text } from "@astryxdesign/core/Text";
import { VStack } from "@astryxdesign/core/VStack";
import * as stylex from "@stylexjs/stylex";
import { Play, Square, X } from "lucide-react";
import { useState } from "react";
import { STAGES } from "../../shared/schema";
import type { Job, QueuedJob } from "../api";
import { useCancelRender, useRemoveQueued } from "../queries";
import { go } from "../route";

const s = stylex.create({
  log: { display: "flex", flexDirection: "column-reverse", maxHeight: 260, overflowY: "auto", marginTop: "var(--spacing-2)" },
  lines: { margin: 0, fontFamily: "var(--font-family-code)", fontSize: "var(--font-size-sm)", whiteSpace: "pre-wrap", wordBreak: "break-word" },
});

const STATE = {
  running: { label: "Being made", variant: "info" },
  done: { label: "Ready", variant: "success" },
  failed: { label: "Failed", variant: "error" },
  cancelled: { label: "Stopped", variant: "neutral" },
} as const;

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

/** The render in progress, how the last one ended, and any queued shorts waiting next. */
export function RenderProgress({ job, queue = [] }: { job: Job; queue?: QueuedJob[] }) {
  const cancel = useCancelRender();
  const removeQueued = useRemoveQueued();
  const [confirming, setConfirming] = useState(false);
  const { stage, scene, scenes } = job.progress;
  const running = job.state === "running";
  const active = job.state === "done" ? STAGES.length : Math.max(stage - 1, 0);
  const elapsed = ((job.finishedAt ?? Date.now()) - job.startedAt) / 1000;
  const inScenes = running && stage === 2 && scene !== null && scenes !== null;
  return (
    <Card>
      <VStack gap={4}>
        <HStack gap={3} align="center" justify="between">
          <VStack gap={1}>
            <Heading level={2}>{job.title}</Heading>
            <Text type="supporting">
              {running ? "Running for" : "Took"} {clock(elapsed)}
              {job.request.videoEngine === "fastmetal" ? " · AI video scenes" : ""}
              {job.request.reuseVisuals ? " · Fast re-assembly" : ""}
            </Text>
          </VStack>
          <HStack gap={2} align="center">
            <Badge label={STATE[job.state].label} variant={STATE[job.state].variant} />
            {running && <Button label="Stop" variant="secondary" size="sm" icon={<Square size={14} />} onClick={() => setConfirming(true)} />}
          </HStack>
        </HStack>
        <Stepper activeStep={active} label="Steps of the render">
          {STAGES.map((label, i) => (
            <Step key={label} step={i} label={label} status={job.state === "failed" && i === active ? "error" : undefined} />
          ))}
        </Stepper>
        {inScenes && <ProgressBar label="Scenes" value={scene - 1} max={scenes} hasValueLabel formatValueLabel={() => `Scene ${scene} of ${scenes}`} />}
        {job.state === "failed" && <Banner status="error" title="The short could not be made" description={job.error} />}
        {job.state === "done" && (
          <Banner status="success" title="Your short is ready" endContent={<Button label="Watch" variant="secondary" size="sm" icon={<Play size={14} />} onClick={() => go({ video: job.video })} />} />
        )}
        {queue.length > 0 && (
          <VStack gap={2}>
            <Text type="label">Up next in queue ({queue.length})</Text>
            {queue.map((item, idx) => (
              <HStack key={item.id} justify="between" align="center" gap={2}>
                <Text size="sm">
                  {idx + 1}. {item.title}
                </Text>
                <IconButton
                  label="Remove from queue"
                  icon={<X size={14} />}
                  size="sm"
                  variant="ghost"
                  onClick={() => removeQueued.mutate(item.id)}
                />
              </HStack>
            ))}
          </VStack>
        )}
        <Collapsible key={job.state === "failed" ? "failed" : "log"} trigger={<Text weight="medium">Log</Text>} defaultIsOpen={job.state === "failed"}>
          <div {...stylex.props(s.log)}>
            <pre {...stylex.props(s.lines)}>{job.log.join("\n") || "Starting…"}</pre>
          </div>
        </Collapsible>
      </VStack>
      <AlertDialog
        isOpen={confirming}
        onOpenChange={setConfirming}
        title="Stop making this short?"
        description="Clips made so far are kept and reused if you make the same short again."
        actionLabel="Stop"
        isActionLoading={cancel.isPending}
        onAction={() => cancel.mutate(undefined, { onSettled: () => setConfirming(false) })}
      />
    </Card>
  );
}
