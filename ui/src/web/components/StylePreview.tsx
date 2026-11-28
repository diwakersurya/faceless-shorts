import { Text } from "@astryxdesign/core/Text";
import * as stylex from "@stylexjs/stylex";
import type { SubtitleStyleChoice } from "../api";

const s = stylex.create({
  box: {
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    alignItems: "center",
    minHeight: 72,
    padding: "var(--spacing-3)",
    borderRadius: "var(--radius-element)",
    background: "linear-gradient(135deg, #17171d 0%, #262235 100%)",
    border: "1px solid var(--color-border)",
    textAlign: "center",
    overflow: "hidden",
  },
  line: {
    fontWeight: 900,
    fontSize: 18,
    lineHeight: 1.2,
    letterSpacing: "0.02em",
  },
  meta: {
    marginTop: "var(--spacing-1)",
  },
});

type Props = {
  styleChoice: SubtitleStyleChoice | undefined;
  enabled: boolean;
  position: string;
  wordsPerScreen: number | null;
};

/** Live preview swatch for the selected subtitle style, highlight color, and outline. */
export function StylePreview({ styleChoice, enabled, position, wordsPerScreen }: Props) {
  if (!enabled) {
    return (
      <div {...stylex.props(s.box)}>
        <Text size="sm" color="secondary">
          Subtitles are turned off
        </Text>
      </div>
    );
  }
  const p = styleChoice?.preview;
  if (!p) return null;
  const words = ["THIS", "CHANGES", "EVERYTHING", "TODAY"].slice(0, wordsPerScreen ?? p.wordsPerScreen);
  const fmt = (w: string) => (p.uppercase ? w : w[0] + w.slice(1).toLowerCase());
  const shadow = `0 2px 6px ${p.outline}, -1px -1px 0 ${p.outline}, 1px -1px 0 ${p.outline}, -1px 1px 0 ${p.outline}, 1px 1px 0 ${p.outline}`;
  return (
    <div {...stylex.props(s.box)}>
      <div {...stylex.props(s.line)} style={{ fontFamily: `"${p.font}", sans-serif`, color: p.primary, textShadow: shadow }}>
        {words.map((w, i) => (
          <span
            key={w}
            style={
              i === 1 || (words.length === 1 && i === 0)
                ? { color: p.highlight, display: "inline-block", transform: "scale(1.12)", marginInline: 4 }
                : { marginInline: 3 }
            }
          >
            {fmt(w)}
          </span>
        ))}
      </div>
      <div {...stylex.props(s.meta)}>
        <Text size="sm" color="secondary">
          {p.font} · {words.length} {words.length === 1 ? "word" : "words"}/screen · {position === "auto" ? "style default" : position}
        </Text>
      </div>
    </div>
  );
}
