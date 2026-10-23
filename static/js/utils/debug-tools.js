/**
 * TEST MODE - the ONE source of truth for every console log and debug tool of the pages.
 *
 * The switch lives on the server (GET /api/test-mode, `TEST_MODE = True/False` in the server's config.py, or the KBOT_TEST_MODE environment variable when config.py has no such line; on when neither is set). This module asks it once,
 * before anything that imports it runs (top-level await), and then:
 *   - testMode / isTestMode()            the answer; false when the server cannot be reached
 *   - window.MIXIN_DEBUG_OVERRIDE        set to it, so the listener tracker and the other old mixins follow it
 *   - console.log / info / debug         silenced when it is off (warn and error always stay: a real fault must be seen)
 *   - registerDebugTool(name, fn, help)  exposes window.kbotDebug[name]; a no-op when it is off
 *   - debugLog(...args)                  console.log when it is on
 * Old code must NOT decide by itself (hostname, a hard-coded true): import { testMode } from here. Every page loads this file first.
 */
async function ask() {
  const k_abort = new AbortController();
  const k_timer = setTimeout(() => k_abort.abort(), 2500);
  try {
    const k_answer = await fetch('/api/test-mode', { cache: 'no-store', signal: k_abort.signal });
    return k_answer.ok && (await k_answer.json()).test_mode === true;
  } catch (_) { return false; } finally { clearTimeout(k_timer); }
}

export const testMode = await ask();
export const isTestMode = () => testMode;
export const isDebug = isTestMode;   // the old name

if (typeof window !== 'undefined') {
  window.MIXIN_DEBUG_OVERRIDE = testMode;
  if (!testMode) { const k_none = () => {}; console.log = k_none; console.info = k_none; console.debug = k_none; }
}

const helps = new Map();

export function registerDebugTool(k_name, k_fn, k_help = '') {
  if (!testMode) return false;
  window.kbotDebug ||= { help: () => { for (const [k_n, k_h] of helps) console.log(`kbotDebug.${k_n}  ${k_h}`); } };
  window.kbotDebug[k_name] = k_fn;
  helps.set(k_name, k_help);
  return true;
}

export function debugLog(...k_args) { if (testMode) console.log(...k_args); }

// Typography (css/typography.css), debug only: try the base size and the families live, e.g. kbotDebug.fontBase(16).
const k_root = typeof document !== 'undefined' ? document.documentElement : null;
registerDebugTool('fontBase', (k_px) => { k_root.style.setProperty('--fs-max', `${Number(k_px)}px`); return `--fs-max = ${Number(k_px)}px`; },
  'fontBase(px) sets the base text size of the whole application at a wide window (default 16)');
registerDebugTool('fontFamily', (k_kind, k_stack) => {
  if (!['ui', 'heading', 'mono'].includes(k_kind)) return "the kinds are 'ui', 'heading', 'mono'";
  k_root.style.setProperty(`--font-${k_kind}`, k_stack); return `--font-${k_kind} = ${k_stack}`;
}, "fontFamily('ui' | 'heading' | 'mono', 'Arial, sans-serif') changes a family everywhere");
registerDebugTool('fontReset', () => { for (const k_name of ['--fs-max', '--fs-fit-min', '--fs-fit-start', '--fs-fit-rate', '--font-ui', '--font-heading', '--font-mono']) k_root.style.removeProperty(k_name); return 'typography: the values of typography.css'; },
  'fontReset() puts the values of typography.css back');

// The text follows the width of the window: a live panel (Ctrl+Alt+F or kbotDebug.fontFit()). Test mode only, and loaded only when asked for.
if (testMode && k_root) {
  let k_panel = null;
  const k_toggle = () => (k_panel ||= import('./font-fit-panel.js').then((k_module) => { k_module.mount(); return k_module; })).then((k_module) => k_module.toggle());
  registerDebugTool('fontFit', k_toggle, 'fontFit() opens or closes the panel that tries how the text follows the window width (Ctrl+Alt+F)');
  document.addEventListener('keydown', (k_event) => { if (k_event.ctrlKey && k_event.altKey && k_event.code === 'KeyF') { k_event.preventDefault(); k_toggle(); } });
}
