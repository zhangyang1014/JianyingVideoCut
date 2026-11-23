import { afterEach, describe, expect, it, vi } from "vitest";
import {
  listProductionPresenters,
  updateProductionPresenter,
} from "./productions-api";
import { presenterConfig } from "./presenters";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("production requests", () => {
  it("rejects malformed catalogs before they can crash the editor", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    for (const data of [
      { items: [] },
      [null],
      [
        {
          id: "lina",
          name: "Lina",
          revision: 1,
          primary_audience: "妈妈",
          address: [],
          guidance: {},
        },
      ],
    ]) {
      fetch.mockResolvedValueOnce({ ok: true, json: async () => data });
      await expect(listProductionPresenters()).rejects.toMatchObject({
        status: 502,
      });
    }
  });
  it("ends stalled reads with a recoverable timeout", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn(
      (_url, options) =>
        new Promise((_resolve, reject) => {
          options.signal.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    vi.stubGlobal("fetch", fetch);
    const result = expect(listProductionPresenters(true)).rejects.toMatchObject(
      { status: 408, message: expect.stringContaining("读取超时") },
    );
    await vi.advanceTimersByTimeAsync(15000);
    await result;
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("bounds response-body reads as well as waiting for headers", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url, options) => ({
        ok: true,
        json: () =>
          new Promise((_resolve, reject) => {
            options.signal.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          }),
      })),
    );
    const result = expect(listProductionPresenters()).rejects.toMatchObject({
      status: 408,
    });
    await vi.advanceTimersByTimeAsync(15000);
    await result;
  });

  it("retains conflict status so the editor can recover with the current revision", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({ detail: "档案已更新" }),
      }),
    );
    const config = presenterConfig();
    await expect(
      updateProductionPresenter(
        { ...config, id: "lina", revision: 2, archived: false },
        config,
      ),
    ).rejects.toMatchObject({ status: 409, message: "档案已更新" });
  });

  it("explains invalid service responses and releases the timeout on success", async () => {
    vi.useFakeTimers();
    const fetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => {
          throw new SyntaxError("html");
        },
      })
      .mockResolvedValueOnce({ ok: true, json: async () => [] });
    vi.stubGlobal("fetch", fetch);
    await expect(listProductionPresenters()).rejects.toMatchObject({
      status: 502,
    });
    await expect(listProductionPresenters()).resolves.toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
  });
});
