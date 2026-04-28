import { useState, useCallback, useRef } from "react";

/**
 * 通用撤销/重做栈 Hook。
 * 调用方在每次可变操作前 push(当前快照)，之后可通过 undo/redo 回退/前进。
 */
export function useUndoHistory<T>(maxSize = 50) {
  const pastRef = useRef<T[]>([]);
  const futureRef = useRef<T[]>([]);
  const [revision, setRevision] = useState(0);

  const forceUpdate = useCallback(() => setRevision((r) => r + 1), []);

  const push = useCallback(
    (snapshot: T) => {
      pastRef.current = [...pastRef.current, snapshot].slice(-maxSize);
      futureRef.current = [];
      forceUpdate();
    },
    [maxSize, forceUpdate],
  );

  const undo = useCallback(
    (current: T): T | null => {
      if (pastRef.current.length === 0) return null;
      const prev = pastRef.current[pastRef.current.length - 1];
      pastRef.current = pastRef.current.slice(0, -1);
      futureRef.current = [...futureRef.current, current];
      forceUpdate();
      return prev;
    },
    [forceUpdate],
  );

  const redo = useCallback(
    (current: T): T | null => {
      if (futureRef.current.length === 0) return null;
      const next = futureRef.current[futureRef.current.length - 1];
      futureRef.current = futureRef.current.slice(0, -1);
      pastRef.current = [...pastRef.current, current];
      forceUpdate();
      return next;
    },
    [forceUpdate],
  );

  const pushBack = useCallback(
    (snapshot: T) => {
      pastRef.current = [...pastRef.current, snapshot];
      forceUpdate();
    },
    [forceUpdate],
  );

  const popFuture = useCallback(
    (): T | null => {
      if (futureRef.current.length === 0) return null;
      const top = futureRef.current[futureRef.current.length - 1];
      futureRef.current = futureRef.current.slice(0, -1);
      forceUpdate();
      return top;
    },
    [forceUpdate],
  );

  const clear = useCallback(() => {
    pastRef.current = [];
    futureRef.current = [];
    forceUpdate();
  }, [forceUpdate]);

  const canUndo = pastRef.current.length > 0;
  const canRedo = futureRef.current.length > 0;

  return { push, undo, redo, pushBack, popFuture, clear, canUndo, canRedo } as const;
}
