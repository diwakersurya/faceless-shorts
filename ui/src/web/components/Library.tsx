import { AspectRatio } from "@astryxdesign/core/AspectRatio";
import { ClickableCard } from "@astryxdesign/core/ClickableCard";
import { EmptyState } from "@astryxdesign/core/EmptyState";
import { Grid } from "@astryxdesign/core/Grid";
import { Heading } from "@astryxdesign/core/Heading";
import { Skeleton } from "@astryxdesign/core/Skeleton";
import { Text } from "@astryxdesign/core/Text";
import { Timestamp } from "@astryxdesign/core/Timestamp";
import { VStack } from "@astryxdesign/core/VStack";
import * as stylex from "@stylexjs/stylex";
import { Film } from "lucide-react";
import { type Video, api } from "../api";
import { format } from "../route";

const s = stylex.create({
  frame: { width: "100%", height: "100%", objectFit: "cover", borderRadius: "var(--radius-element)", backgroundColor: "#000" },});

export const megabytes = (bytes: number) => `${(bytes / 1e6).toFixed(1)} MB`;
const COLUMNS = { minWidth: 170 };

function VideoCard({ video }: { video: Video }) {
  const title = video.title ?? video.name;
  return (
    <ClickableCard label={`Open ${title}`} href={format({ video: video.name })} padding={2}>
      <VStack gap={2}>
        <AspectRatio ratio={9 / 16}>
          {/* A still made by the server: a phone does not have to open each video to show the list. */}
          <img {...stylex.props(s.frame)} src={api.posterUrl(video.name)} alt="" loading="lazy" />
        </AspectRatio>
        <Text weight="medium" maxLines={2}>
          {title}
        </Text>
        <Text type="supporting">
          <Timestamp value={video.createdAt} format="relative" /> · {megabytes(video.size)}
        </Text>
      </VStack>
    </ClickableCard>
  );
}

/** Every short made so far, newest first. undefined = still loading. */
export function Library({ videos }: { videos: Video[] | undefined }) {
  return (
    <VStack gap={3}>
      <Heading level={2}>Your shorts</Heading>
      {!videos ? (
        <Grid columns={COLUMNS} gap={4}>
          {[0, 1, 2].map((i) => (
            <AspectRatio key={i} ratio={9 / 19}>
              <Skeleton index={i} />
            </AspectRatio>
          ))}
        </Grid>
      ) : videos.length === 0 ? (
        <EmptyState icon={<Film size={32} />} title="No shorts yet" description="Describe a topic on the left and make the first one. It appears here when it is ready." />
      ) : (
        <Grid columns={COLUMNS} gap={4}>
          {videos.map((v) => (
            <VideoCard key={v.name} video={v} />
          ))}
        </Grid>
      )}
    </VStack>
  );
}
