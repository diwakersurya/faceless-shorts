import { IconButton } from "@astryxdesign/core/IconButton";
import { Square, Volume2 } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../api";
import { toast } from "../toast";

type Props = {
  engine: string;
  voice: string;
  language: string;
  rate?: string;
  voiceStyle?: string;
};

/** Plays a short 2-second sample of the currently selected voice. */
export function VoicePreviewButton({ engine, voice, language, rate, voiceStyle }: Props) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");

  const stop = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    setState("idle");
  };

  const toggle = async () => {
    if (state !== "idle") {
      stop();
      return;
    }
    setState("loading");
    try {
      const url = api.voicePreviewUrl({ engine, voice, language, rate, voiceStyle });
      const res = await fetch(url);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || "Could not preview this voice.");
      }
      const blob = await res.blob();
      const objUrl = URL.createObjectURL(blob);
      const audio = new Audio(objUrl);
      audioRef.current = audio;
      audio.onended = () => {
        URL.revokeObjectURL(objUrl);
        stop();
      };
      audio.onerror = () => {
        URL.revokeObjectURL(objUrl);
        stop();
        toast.bad("Could not play the voice sample.");
      };
      setState("playing");
      await audio.play();
    } catch (e) {
      setState("idle");
      toast.bad(e instanceof Error ? e.message : "Voice preview failed");
    }
  };

  return (
    <IconButton
      label={state === "playing" ? "Stop voice preview" : "Preview voice"}
      tooltip={state === "playing" ? "Stop preview" : "Hear a 2-second sample"}
      icon={state === "playing" ? <Square size={16} /> : <Volume2 size={16} />}
      variant="secondary"
      isLoading={state === "loading"}
      onClick={toggle}
    />
  );
}
