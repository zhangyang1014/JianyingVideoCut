import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { createPlaybackClock } from "@/lib/playback-clock";
import { usePlaybackTime } from "./usePlaybackTime";

afterEach(cleanup);

it("updates only the subscribed subtitle while the parent and inactive rows stay unchanged", () => {
  const clock = createPlaybackClock();
  const renders = { parent: 0, active: 0, inactive: 0 };
  function Row({ active }: { active: boolean }) {
    renders[active ? "active" : "inactive"]++;
    const time = usePlaybackTime(clock, active);
    return <output aria-label={active ? "active" : "inactive"}>{time}</output>;
  }
  function Workbench() {
    renders.parent++;
    return <><Row active /><Row active={false} /></>;
  }
  render(<Workbench />);
  for (let frame = 1; frame <= 60; frame++) act(() => clock.setTime(frame / 60));
  expect(screen.getByLabelText("active").textContent).toBe("1");
  expect(screen.getByLabelText("inactive").textContent).toBe("-1");
  expect(renders).toEqual({ parent: 1, active: 61, inactive: 1 });
});

it("reads the latest frame on subscription and detaches on deactivation or unmount", () => {
  const clock = createPlaybackClock();
  const listener = vi.fn();
  const unsubscribe = clock.subscribe(listener);
  function Row({ active }: { active: boolean }) {
    return <output>{usePlaybackTime(clock, active)}</output>;
  }
  clock.setTime(40);
  const view = render(<Row active />);
  expect(screen.getByRole("status").textContent).toBe("40");
  view.rerender(<Row active={false} />);
  act(() => clock.setTime(42));
  expect(screen.getByRole("status").textContent).toBe("-1");
  view.rerender(<Row active />);
  expect(screen.getByRole("status").textContent).toBe("42");
  view.unmount(); unsubscribe();
  clock.setTime(43); clock.setTime(NaN); clock.setTime(Infinity);
  expect(listener).toHaveBeenCalledTimes(2);
  expect(clock.getTime()).toBe(43);
});
