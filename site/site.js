// @ts-check
/**
 * Shared behaviour for every page: the one orchestrated entrance, the split flap, and the
 * small helpers the page scripts reuse. Type checked with `npm run typecheck` (TypeScript
 * in checkJs mode) and shipped to the browser unchanged, so the deploy stays a file copy.
 */

/** True when the visitor has asked the system for less motion. */
export const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

requestAnimationFrame(() => document.body.classList.add("go"));

/**
 * @param {string} id
 * @returns {HTMLElement}
 */
export function need(id) {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el;
}

/**
 * Build an element. Attribute names are passed through except `class`, `text` and `html`.
 * @param {string} tag
 * @param {Record<string, string>} [attrs]
 * @param {(Node|string)[]} [kids]
 * @returns {HTMLElement}
 */
export function el(tag, attrs = {}, kids = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  for (const kid of kids) node.append(kid);
  return node;
}

/** @param {number|null|undefined} n */
export const int = (n) => (n == null ? "–" : Math.round(n).toLocaleString("en-IE"));
/** @param {number|null|undefined} n */
export const secs = (n) => (n == null ? "–" : Math.round(n) + "s");
/** @param {number|null|undefined} n @param {number} [d] */
export const pct = (n, d = 1) => (n == null ? "–" : n.toFixed(d) + "%");
/** @param {string|null|undefined} t */
export const hhmm = (t) => (t || "").slice(0, 5);

/**
 * Render a time into a split-flap display. Only the characters listed in `turn` animate,
 * because a flap that moves when nothing changed is decoration rather than information.
 * @param {Element} host
 * @param {string} time
 * @param {number[]} [turn] indices of characters that changed
 */
export function flap(host, time, turn = [3, 4]) {
  host.replaceChildren(
    ...[...time].map((ch, i) => {
      const s = el("span", { text: ch });
      if (!reducedMotion && turn.includes(i)) {
        s.classList.add("turn");
        s.style.animationDelay = i * 90 + "ms";
      }
      return s;
    })
  );
}

/**
 * Call `onEnter` with the index of whichever `[data-step]` element is nearest the middle of
 * the viewport. Used by the method walkthrough on the landing page.
 * @param {NodeListOf<Element>|Element[]} steps
 * @param {(index: number, step: Element) => void} onEnter
 */
export function watchSteps(steps, onEnter) {
  const list = [...steps];
  const io = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        for (const s of list) s.classList.toggle("on", s === entry.target);
        const raw = /** @type {HTMLElement} */ (entry.target).dataset.step;
        onEnter(Number(raw), entry.target);
      }
    },
    { rootMargin: "-45% 0px -45% 0px", threshold: 0 }
  );
  for (const s of list) io.observe(s);
  return io;
}
