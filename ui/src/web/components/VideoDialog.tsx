import { AlertDialog } from "@astryxdesign/core/AlertDialog";
import { Button } from "@astryxdesign/core/Button";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { EmptyState } from "@astryxdesign/core/EmptyState";
import { HStack } from "@astryxdesign/core/HStack";
import { MetadataList, MetadataListItem } from "@astryxdesign/core/MetadataList";
import { Text } from "@astryxdesign/core/Text";
import { Timestamp } from "@astryxdesign/core/Timestamp";
import { Token } from "@astryxdesign/core/Token";
import { VStack } from "@astryxdesign/core/VStack";
import * as stylex from "@stylexjs/stylex";
import { Copy, Download, FileX, Trash2 } from "lucide-react";
import { useState } from "react";
import { type Video, api } from "../api";
import { copyText } from "../clipboard";
import { useDeleteVideo } from "../queries";
import { go } from "../route";
import { megabytes } from "./Library";

const NARROW = "@media (max-width: 760px)";

const s = stylex.create({
  // The video beside what to post with it; one under the other on a phone.
  body: { display: "grid", gridTemplateColumns: { default: "minmax(0, 300px) minmax(0, 1fr)", [NARROW]: "minmax(0, 1fr)" }, gap: "var(--spacing-5)", padding: "var(--spacing-4)" },
  player: { width: "100%", aspectRatio: "9 / 16", borderRadius: "var(--radius-element)", backgroundColor: "#000" },
});

const close = () => go({ video: null });
const onOpenChange = (open: boolean) => {
  if (!open) close();
};

function Details({ video }: { video: Video }) {
  const remove = useDeleteVideo();
  const [confirming, setConfirming] = useState(false);
  const hashtags = video.tags.map((t) => "#" + t).join(" ");
  return (
    <div {...stylex.props(s.body)}>
      <video {...stylex.props(s.player)} src={api.videoUrl(video.name)} controls autoPlay playsInline />
      <VStack gap={4}>
        {video.caption && (
          <VStack gap={2}>
            <Text type="label">Caption</Text>
            <Text>{video.caption}</Text>
          </VStack>
        )}
        {video.tags.length > 0 && (
          <VStack gap={2}>
            <Text type="label">Tags</Text>
            <HStack gap={1} wrap="wrap">
              {video.tags.map((t) => (
                <Token key={t} label={"#" + t} size="sm" />
              ))}
            </HStack>
          </VStack>
        )}
        <HStack gap={2} wrap="wrap">
          {video.title && <Button label="Copy title" size="sm" icon={<Copy size={14} />} onClick={() => copyText(video.title!, "Title copied")} />}
          {video.caption && <Button label="Copy caption" size="sm" icon={<Copy size={14} />} onClick={() => copyText(video.caption!, "Caption copied")} />}
          {hashtags && <Button label="Copy tags" size="sm" icon={<Copy size={14} />} onClick={() => copyText(hashtags, "Tags copied")} />}
        </HStack>
        <MetadataList>
          <MetadataListItem label="Made">
            <Timestamp value={video.createdAt} format="date_time" />
          </MetadataListItem>
          <MetadataListItem label="Size">{megabytes(video.size)}</MetadataListItem>
          <MetadataListItem label="File">{video.name}</MetadataListItem>
        </MetadataList>
        <HStack gap={2} justify="between">
          <Button label="Download" variant="primary" icon={<Download size={16} />} href={api.downloadUrl(video.name)} />
          <Button label="Delete" variant="destructive" icon={<Trash2 size={16} />} onClick={() => setConfirming(true)} />
        </HStack>
      </VStack>
      <AlertDialog
        isOpen={confirming}
        onOpenChange={setConfirming}
        title="Delete this short?"
        description="The video file and its title, caption and tags are removed from the output folder. This cannot be undone."
        actionLabel="Delete short"
        isActionLoading={remove.isPending}
        onAction={() => remove.mutate(video.name, { onSettled: () => setConfirming(false) })}
      />
    </div>
  );
}

/** One short, opened from the list or from a link. `videos` undefined = the list is still loading. */
export function VideoDialog({ name, videos }: { name: string; videos: Video[] | undefined }) {
  const video = videos?.find((v) => v.name === name);
  return (
    <Dialog isOpen onOpenChange={onOpenChange} purpose="info" width={820} maxHeight="90dvh">
      <DialogHeader title={video?.title ?? name} onOpenChange={onOpenChange} />
      {video ? (
        <Details key={video.name} video={video} />
      ) : videos ? (
        <EmptyState icon={<FileX size={32} />} title="This short is no longer there" description="It may have been deleted." actions={<Button label="Back to your shorts" onClick={close} />} />
      ) : null}
    </Dialog>
  );
}
