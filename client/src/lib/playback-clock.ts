/** A task-local clock: frame updates notify the active subtitle, not the workbench. */
export function createPlaybackClock() {
  let time = 0;
  const listeners = new Set<() => void>();
  return {
    getTime: () => time,
    setTime(next: number) {
      if (!Number.isFinite(next) || next === time) return;
      time = next;
      listeners.forEach((listener) => listener());
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}

export type PlaybackClock = ReturnType<typeof createPlaybackClock>;
