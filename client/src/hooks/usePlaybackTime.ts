import { useSyncExternalStore } from "react";
import type { PlaybackClock } from "@/lib/playback-clock";

const noSubscription = () => () => {};

export function usePlaybackTime(clock: PlaybackClock | undefined, active: boolean, fallback = -1) {
  return useSyncExternalStore(
    active && clock ? clock.subscribe : noSubscription,
    () => active && clock ? clock.getTime() : fallback,
  );
}
