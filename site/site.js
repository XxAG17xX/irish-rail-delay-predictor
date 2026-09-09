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
 * Build an element. Attribute names are passed through except `class` and `text`.
 *
 * There is deliberately no `html` option. Every string this page renders comes from the
 * API or from accuracy.json, and an innerHTML sink sitting unused is the thing a later
 * edit reaches for without thinking. textContent cannot execute anything, so the page has
 * no XSS surface at all rather than one that is currently unreachable.
 *
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
      if (ch === ":") {
        s.classList.add("sep");
        return s;
      }
      if (!reducedMotion && turn.includes(i)) {
        s.classList.add("turn");
        s.style.animationDelay = i * 90 + "ms";
      }
      return s;
    })
  );
}

/**
 * A dismissible notice in the page, used for the states a visitor otherwise meets as
 * silence: the service refusing them, or being unreachable. `countdown` seconds, when
 * given, ticks down in the text and calls `onExpiry` when it reaches zero.
 *
 * @param {HTMLElement} host
 * @param {object} opts
 * @param {"caution"|"danger"} opts.level
 * @param {string} opts.title
 * @param {string} opts.body
 * @param {number} [opts.countdown]
 * @param {() => void} [opts.onExpiry]
 * @param {{label: string, action: () => void}} [opts.action]
 */
export function notice(host, opts) {
  const lamp = el("span", { class: `lamp lamp-${opts.level} mt-1.5 shrink-0` });
  const heading = el("p", { class: "font-semibold", text: opts.title });
  const body = el("p", { class: "mt-1.5 max-w-xl text-sm leading-relaxed text-ink-2", text: opts.body });
  const column = el("div", {}, [heading, body]);
  const panel = el("div", { class: "panel px-5 py-4", role: "status", "aria-live": "polite" }, [
    el("div", { class: "flex items-start gap-3" }, [lamp, column]),
  ]);
  host.replaceChildren(panel);

  let timer = 0;
  if (opts.countdown != null) {
    let left = opts.countdown;
    const line = el("p", { class: "mt-2 text-sm text-caution" });
    column.append(line);
    const tick = () => {
      line.textContent =
        left > 0 ? `Trying again in ${left} second${left === 1 ? "" : "s"}.` : "Trying again now.";
      if (left-- <= 0) {
        clearInterval(timer);
        opts.onExpiry?.();
      }
    };
    tick();
    timer = setInterval(tick, 1000);
  }

  if (opts.action) {
    const button = el("button", { class: "btn btn-ghost mt-3", type: "button", text: opts.action.label });
    button.addEventListener("click", () => {
      clearInterval(timer);
      opts.action?.action();
    });
    column.append(button);
  }
  return () => clearInterval(timer);
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
