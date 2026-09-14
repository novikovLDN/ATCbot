/**
 * Wrapper around the View Transitions API.
 *
 * The effect it enables — a table row morphing into the detail card, the
 * number physically travelling from cell to heading — is the one piece of
 * motion in this panel that earns its cost, because it answers "where did
 * I come from" rather than decorating the answer.
 *
 * Three guards, all of which must hold or the update simply happens
 * instantly:
 *
 *   · Firefox has not shipped same-document transitions, so the API is
 *     feature-detected rather than assumed. Nothing breaks without it;
 *     this is progressive enhancement, not a dependency.
 *   · An operator who asked for reduced motion means it. The OS-level
 *     query and the panel's own toggle are both honoured.
 *   · A transition already in flight is not interrupted with another —
 *     rapid clicks through a list would otherwise queue up morphs that
 *     land after the user has moved on.
 */

type DocumentWithVT = Document & {
  startViewTransition?: (cb: () => void | Promise<void>) => {
    finished: Promise<void>;
  };
};

let inFlight = false;

function motionAllowed(): boolean {
  if (document.documentElement.getAttribute("data-motion") === "reduced") {
    return false;
  }
  return !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Runs `update` inside a view transition when the browser and the
 * operator both allow it, and directly otherwise.
 *
 * `update` must perform the state change synchronously — the browser
 * snapshots the DOM the moment the callback returns. Anything awaited
 * inside it lands after the snapshot and will not be animated.
 */
export function withViewTransition(update: () => void): void {
  const doc = document as DocumentWithVT;

  if (inFlight || typeof doc.startViewTransition !== "function" || !motionAllowed()) {
    update();
    return;
  }

  inFlight = true;
  const t = doc.startViewTransition(update);
  t.finished.finally(() => {
    inFlight = false;
  });
}
