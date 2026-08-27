import { useEffect, useRef, useState } from "react";

/**
 * Drives enter/exit transitions for elements that mount/unmount.
 *
 * Returns `{ mounted, visible }`:
 * - `mounted` gates whether the element is in the DOM. It stays true through
 *   the exit so the closing animation can play, then flips false after
 *   `duration` ms.
 * - `visible` is the flag you map to the "open"/"in" CSS class. It flips on one
 *   frame *after* mount (so the browser sees the from-state first and animates
 *   to the to-state) and flips off immediately on close.
 *
 * No animation fires on the very first render: `visible` is seeded to `isOpen`
 * so an element that starts open just appears.
 *
 * `duration` must be >= the longest CSS transition on the element, or it
 * unmounts mid-animation. Honors `prefers-reduced-motion` via the global CSS
 * guard (transitions collapse to ~0ms; the element simply stays mounted for
 * `duration` while already invisible).
 */
export function useMountTransition(isOpen: boolean, duration: number) {
  const [mounted, setMounted] = useState(isOpen);
  const [visible, setVisible] = useState(isOpen);
  const firstRun = useRef(true);

  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    if (isOpen) {
      setMounted(true);
      const raf = requestAnimationFrame(() => setVisible(true));
      return () => cancelAnimationFrame(raf);
    }
    setVisible(false);
    const timer = setTimeout(() => setMounted(false), duration);
    return () => clearTimeout(timer);
  }, [isOpen, duration]);

  return { mounted, visible };
}
