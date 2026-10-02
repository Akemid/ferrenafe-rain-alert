// @vitest-environment jsdom
//
// The one test in this suite that looks at the artifact a resident loads.
//
// `markup.test.ts` asserts the class names appear in the page's stylesheet.
// `page.test.ts` asserts the class names land on the rendered elements. Both
// were true, and both stayed green while the deployed page rendered
// `riesgo inminente` in plain black 16-pixel text, visually identical to
// `sin riesgo` (see `docs/blog/2026-09-28-two-tests-checked-both-ends-and-the-
// middle-was-missing.md`).
//
// What sat between them was the build. Astro scopes a component's `<style>`
// block by rewriting every selector to require a generated attribute —
// `.status-level[data-astro-cid-lcdefpme]` — and it adds that attribute at
// build time only to elements written in the `.astro` template. Every element
// `page.ts` builds with `document.createElement` carries no such attribute, so
// none of those rules could ever match. Neither end of the coupling was wrong;
// the coupling still did not connect.
//
// So this file asserts the middle, and it does so against `dist/`, after a
// real `astro build`, because the transform that broke the page does not exist
// anywhere else. It parses the emitted stylesheet, renders every view state
// into the emitted markup with the real `renderPage`, and asks the only
// question that matters: does each rule the build emitted actually match the
// element the page renders?
//
// What this does NOT cover: whether the colours are correct, legible, or
// distinguishable — only that the rules can match at all. A rule painting
// `riesgo inminente` white on white would pass here.

import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { beforeAll, describe, expect, it } from 'vitest';
import { renderPage, type PageElements } from '../src/lib/page';
import type { Snapshot } from '../src/lib/snapshot';
import { resolveViewState } from '../src/lib/view';
import {
  sampleAlerting,
  sampleBlind,
  sampleDegradedForecastOnly,
  sampleQuiet,
} from './fixtures';

/** `none` from a cycle where only one source answered: incomplete, not blind. */
const sampleQuietIncomplete: Snapshot = {
  ...sampleQuiet,
  sources: { senamhi: 'unavailable', open_meteo: 'available' },
  degraded: true,
};

const WEB_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DIST = join(WEB_ROOT, 'dist');

/** One hour after every fixture's `evaluated_at`, as in `page.test.ts`. */
const FRESH = new Date('2026-09-03T08:00:00-05:00');
/** Three weeks after it — well past `STALE_AFTER_HOURS`. */
const STALE = new Date('2026-09-24T14:00:00-05:00');

// --- The built artifact ---------------------------------------------------

/**
 * Builds the site. The suite must not read a `dist/` left over from an older
 * source tree: a stale build would report on a page nobody is about to ship,
 * which is a smaller version of the defect this file exists to catch.
 */
function build(): void {
  execFileSync('npm', ['run', 'build'], { cwd: WEB_ROOT, stdio: 'pipe' });
}

/**
 * Loads `dist/index.html` into the jsdom document, so that `renderPage` —
 * which calls `document.createElement` — renders into the real built markup,
 * against the real built stylesheet, with the real element ids.
 *
 * `document.write` rather than assigning `innerHTML`: the scoping attribute
 * Astro adds to `<html>` and `<body>` is part of what is under test, and
 * `innerHTML` on `documentElement` would drop it.
 */
function loadBuiltPage(): void {
  const html = readFileSync(join(DIST, 'index.html'), 'utf-8');
  document.open();
  document.write(html);
  document.close();

  // Astro inlines a small stylesheet into the page and links a large one, and
  // the threshold is not ours to depend on. jsdom does not fetch `<link>`
  // targets, so any linked sheet is read off disk and inlined here — under
  // either build outcome the rules below are the rules the browser gets.
  for (const link of Array.from(document.querySelectorAll('link[rel="stylesheet"]'))) {
    const href = link.getAttribute('href');
    if (href === null || !href.startsWith('/')) continue;
    const style = document.createElement('style');
    style.textContent = readFileSync(join(DIST, href.slice(1)), 'utf-8');
    document.head.appendChild(style);
  }
}

/**
 * Every style rule in every sheet, with the ones inside an at-rule (`@media`)
 * flattened in. `inMedia` records which came from one: a rule that only exists
 * inside `@media (min-width: …)` is part of what a desktop resident loads, and
 * it must be held to the same "can it match anything" standard as the rest.
 */
interface CollectedRule {
  readonly rule: CSSStyleRule;
  readonly inMedia: boolean;
}

function collectStyleRules(): CollectedRule[] {
  const found: CollectedRule[] = [];
  const visit = (rules: CSSRuleList, inMedia: boolean): void => {
    for (const rule of Array.from(rules)) {
      if ('selectorText' in rule) found.push({ rule: rule as CSSStyleRule, inMedia });
      else if ('cssRules' in rule) visit((rule as CSSGroupingRule).cssRules, inMedia || 'media' in rule);
    }
  };
  for (const sheet of Array.from(document.styleSheets)) visit(sheet.cssRules, false);
  return found;
}

// --- Selector anatomy -----------------------------------------------------

/**
 * The subject of a selector is its rightmost compound — the element the rule
 * actually paints. In `.level-imminent[cid] .status-level[cid]` the subject is
 * `.status-level[cid]`; the `.level-imminent[cid]` half constrains an
 * ancestor, and that ancestor (the `<section>`) IS in the template, which is
 * exactly why half of this stylesheet worked and half did not.
 *
 * Splitting on whitespace and combinators is safe for the selectors this page
 * emits. It would mis-split an attribute selector containing a space or a `>`
 * inside quotes; there is none, and a scoping attribute cannot contain one.
 */
function subjectCompound(selector: string): string {
  const parts = selector.trim().split(/\s*[>+~]\s*|\s+/).filter((part) => part.length > 0);
  return parts.length === 0 ? '' : (parts[parts.length - 1] as string);
}

function classesIn(compound: string): string[] {
  return Array.from(compound.matchAll(/\.([A-Za-z_-][\w-]*)/g), (match) => match[1] as string);
}

/** Astro's build-time scoping attribute, in any of its generated forms. */
const SCOPING_ATTRIBUTE = /\[data-astro-cid-[^\]]*\]/;

function individualSelectors(rules: readonly CSSStyleRule[]): string[] {
  return rules.flatMap((rule) => rule.selectorText.split(',').map((part) => part.trim())).filter((part) => part.length > 0);
}

/**
 * The selector as `querySelector` can ask it. A pseudo-element
 * (`.status-level::first-letter`) styles part of an element and is not
 * something the DOM can select, so jsdom throws on it. The question this file
 * asks is whether the ELEMENT the rule targets exists, so the pseudo-element
 * is dropped before asking.
 */
function queryable(selector: string): string {
  return selector.replace(/::?(first-letter|first-line|before|after)\b/g, '');
}

// --- Rendering every state into the built page ----------------------------

function elementsFromBuiltPage(): PageElements {
  return {
    status: document.getElementById('status') as HTMLElement,
    recentList: document.getElementById('recent-alerts-list') as HTMLUListElement,
    recentEmpty: document.getElementById('recent-alerts-empty') as HTMLElement,
    recentError: document.getElementById('recent-alerts-error') as HTMLElement,
  };
}

interface RenderedState {
  readonly name: string;
  readonly render: (elements: PageElements) => void;
}

/**
 * Every state that paints something. Between them these cover each class the
 * page can put on an element: the three level colours, the degraded override
 * that stops a blind cycle reading as a green all-clear, the stale frame and
 * its banner, and the error frame.
 */
const STATES: readonly RenderedState[] = [
  { name: 'fresh, no risk', render: (e) => renderState(e, sampleQuiet, FRESH) },
  { name: 'fresh, imminent', render: (e) => renderState(e, sampleAlerting, FRESH) },
  { name: 'fresh, prepare, one source down', render: (e) => renderState(e, sampleDegradedForecastOnly, FRESH) },
  { name: 'fresh, no risk, one source down', render: (e) => renderState(e, sampleQuietIncomplete, FRESH) },
  { name: 'fresh, blind cycle', render: (e) => renderState(e, sampleBlind, FRESH) },
  { name: 'stale', render: (e) => renderState(e, sampleAlerting, STALE) },
  {
    name: 'failed fetch',
    render: (e) => renderPage(e, resolveViewState({ ok: false, error: new Error('network unreachable') }, FRESH)),
  },
];

function renderState(elements: PageElements, snapshot: Snapshot, now: Date): void {
  renderPage(elements, resolveViewState({ ok: true, snapshot }, now));
}

interface Observation {
  /** Selectors from the built stylesheet that matched a rendered element. */
  readonly matched: Set<string>;
  /** Classes the page put on any element, template-written or created. */
  readonly rendered: Set<string>;
  /** Classes the page put on elements it created itself, which carry no
   * template attributes and are the half that silently went unstyled. */
  readonly onCreatedElements: Set<string>;
}

function observe(selectors: readonly string[]): Observation {
  const matched = new Set<string>();
  const rendered = new Set<string>();
  const onCreatedElements = new Set<string>();

  for (const state of STATES) {
    loadBuiltPage();
    // Everything present before rendering came from the template and carries
    // whatever attributes the build gave it. Anything present afterwards that
    // was not in this set was built by `document.createElement` in `page.ts`.
    // Read off the real documents rather than inferred from the attribute
    // itself, so the distinction survives a change in how Astro marks them.
    const fromTemplate = new Set(Array.from(document.querySelectorAll('*')));

    state.render(elementsFromBuiltPage());

    for (const element of Array.from(document.querySelectorAll('*'))) {
      for (const className of Array.from(element.classList)) {
        rendered.add(className);
        if (!fromTemplate.has(element)) onCreatedElements.add(className);
      }
    }
    for (const selector of selectors) {
      if (document.querySelector(queryable(selector)) !== null) matched.add(selector);
    }
  }

  // The loading block is static markup in the template and `renderPage` removes
  // it, so it is gone from every state above. Its rules are checked against the
  // page as built, before any rendering, or they would be exempt.
  loadBuiltPage();
  for (const element of Array.from(document.querySelectorAll('*'))) {
    for (const className of Array.from(element.classList)) rendered.add(className);
  }
  for (const selector of selectors) {
    if (document.querySelector(queryable(selector)) !== null) matched.add(selector);
  }

  return { matched, rendered, onCreatedElements };
}

// --- The assertions -------------------------------------------------------

let collected: CollectedRule[] = [];
let selectors: string[] = [];
let observation: Observation;

beforeAll(() => {
  build();
  expect(existsSync(join(DIST, 'index.html'))).toBe(true);
  loadBuiltPage();
  collected = collectStyleRules();
  selectors = individualSelectors(collected.map(({ rule }) => rule));
  observation = observe(selectors);
}, 180_000);

describe('the built stylesheet must be able to reach what the page renders', () => {
  it('matches every rule it emits for a class the page renders', () => {
    const targeting = selectors.filter((selector) =>
      classesIn(subjectCompound(selector)).some((className) => observation.rendered.has(className)),
    );

    // Without this the assertion below passes on an empty stylesheet — and an
    // empty stylesheet is the very failure being tested, arriving by a
    // different route.
    expect(targeting.length).toBeGreaterThan(0);

    const unreachable = targeting.filter((selector) => !observation.matched.has(selector));
    expect(unreachable).toEqual([]);
  });

  it('also checks the rules that only apply inside a media query', () => {
    // The desktop layout lives in `@media (min-width: …)`. `collectStyleRules`
    // descends into it, but nothing would notice if a build step started
    // emitting those rules in a form it stopped descending into — the check
    // above would keep passing on the mobile rules alone. So assert the
    // media rules were actually collected, and that they target rendered
    // classes (a media block of rules for nothing would pass vacuously).
    const inMedia = individualSelectors(collected.filter(({ inMedia }) => inMedia).map(({ rule }) => rule));

    expect(inMedia.length).toBeGreaterThan(0);
    const targeting = inMedia.filter((selector) =>
      classesIn(subjectCompound(selector)).some((className) => observation.rendered.has(className)),
    );
    expect(targeting.length).toBeGreaterThan(0);
    expect(targeting.filter((selector) => !observation.matched.has(selector))).toEqual([]);
  });

  it('does not require a build-time scoping attribute on an element the page creates', () => {
    // The mechanism, named. The test above fails for any reason a rule cannot
    // match; this one says which reason broke the deployed page, so the next
    // person reading a red run does not have to rediscover it.
    expect(observation.onCreatedElements.size).toBeGreaterThan(0);

    const scoped = selectors.filter((selector) => {
      const subject = subjectCompound(selector);
      return (
        SCOPING_ATTRIBUTE.test(subject) &&
        classesIn(subject).some((className) => observation.onCreatedElements.has(className))
      );
    });
    expect(scoped).toEqual([]);
  });
});

const GREEN = /^(#1a7f37|rgb\(26,\s*127,\s*55\))$/i;

/** Renders one state into the built page and returns the current segment's bar and the heading. */
function renderCurrent(snapshot: Snapshot, now: Date): { bar: HTMLElement; heading: HTMLElement } {
  loadBuiltPage();
  renderState(elementsFromBuiltPage(), snapshot, now);
  return {
    bar: document.querySelector('.is-current .scale-bar') as HTMLElement,
    heading: document.querySelector('.status-level') as HTMLElement,
  };
}

describe('a stale or incomplete "none" must not be painted green, whatever the rule order', () => {
  const cases: ReadonlyArray<[string, Snapshot, Date]> = [
    ['stale, none', sampleQuiet, STALE],
    ['incomplete, none', sampleQuietIncomplete, FRESH],
  ];

  it.each(cases)('%s: the bar and the heading are not green', (_name, snapshot, now) => {
    const { bar, heading } = renderCurrent(snapshot, now);
    const style = getComputedStyle(bar);

    expect(bar).not.toBeNull();
    expect(style.backgroundColor).not.toMatch(GREEN);
    expect(style.background).not.toMatch(/#1a7f37|rgb\(26,\s*127,\s*55\)/i);
    expect(getComputedStyle(heading).color).not.toMatch(GREEN);
  });

  it('stale: the bar is white with the 3px solid outline', () => {
    const { bar } = renderCurrent(sampleQuiet, STALE);
    const style = getComputedStyle(bar);

    expect(style.backgroundColor).toMatch(/^(#fff|#ffffff|white|rgb\(255,\s*255,\s*255\))$/i);
    expect(style.borderTopWidth).toBe('3px');
    expect(style.borderTopStyle).toBe('solid');
    expect(style.borderTopColor).toMatch(/^(#3a3a38|rgb\(58,\s*58,\s*56\))$/i);
  });

  it('incomplete, none: the bar is hatched, not a flat green', () => {
    const { bar } = renderCurrent(sampleQuietIncomplete, FRESH);
    const style = getComputedStyle(bar);

    expect(style.backgroundColor).not.toMatch(GREEN);
    // jsdom does not resolve gradients into computed style, so the gradient is
    // asserted on the rule that targets the hatched current bar.
    const hatchedRule = collectStyleRules()
      .map(({ rule }) => rule)
      .find((rule) => rule.selectorText.includes('scale-hatched') && rule.selectorText.includes('.scale-bar'));
    expect(hatchedRule?.cssText).toContain('repeating-linear-gradient');
  });
});

describe('the current segment is marked by more than its colour', () => {
  const DARK = /^(#1b1b1b|rgb\(27,\s*27,\s*27\))$/i;

  it.each([
    ['none', sampleQuiet],
    ['prepare', sampleDegradedForecastOnly],
    ['imminent', sampleAlerting],
  ] as const)('the current "%s" bar has a 2px solid #1b1b1b border', (_level, snapshot) => {
    const { bar } = renderCurrent(snapshot, FRESH);
    const style = getComputedStyle(bar);

    expect(style.borderTopWidth).toBe('2px');
    expect(style.borderTopStyle).toBe('solid');
    expect(style.borderTopColor).toMatch(DARK);
    expect(style.boxSizing).toBe('border-box');
  });

  it('the stale outline (3px #3a3a38) wins over the current-marker border', () => {
    const { bar } = renderCurrent(sampleAlerting, STALE);
    const style = getComputedStyle(bar);

    expect(style.borderTopWidth).toBe('3px');
    expect(style.borderTopColor).toMatch(/^(#3a3a38|rgb\(58,\s*58,\s*56\))$/i);
  });

  it('the blind scale keeps dashed outlines and has no current segment', () => {
    loadBuiltPage();
    renderState(elementsFromBuiltPage(), sampleBlind, FRESH);
    const bar = document.querySelector('.scale-bar') as HTMLElement;

    expect(document.querySelector('.is-current')).toBeNull();
    expect(getComputedStyle(bar).borderTopStyle).toBe('dashed');
    expect(getComputedStyle(bar).borderTopWidth).toBe('2px');
  });
});

describe('the no-JavaScript fallback', () => {
  it('sits in a block that has the page horizontal padding', () => {
    loadBuiltPage();
    const noscript = document.querySelector('#status noscript') as HTMLElement;
    expect(noscript).not.toBeNull();

    // With scripting on, jsdom keeps noscript content as text; parse it as a
    // browser with scripting off would.
    const holder = document.createElement('div');
    holder.innerHTML = noscript.innerHTML.replace(/&lt;/g, '<').replace(/&gt;/g, '>');
    const notice = holder.querySelector('.noscript-notice') as HTMLElement;
    expect(notice).not.toBeNull();
    expect(notice.textContent).toContain('Esta página necesita JavaScript');

    const rule = collectStyleRules()
      .map(({ rule }) => rule)
      .find((candidate) => candidate.selectorText.split(',').map((s) => s.trim()).includes('.noscript-notice'));
    expect(rule).toBeDefined();
    expect(rule?.cssText).toMatch(/padding[^;]*var\(--pad-x\)/);
  });
});
