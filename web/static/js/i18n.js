/* DotServe i18n loader
 * -----------------------
 * Minimal, dependency-free translation loader used by app.js / index.html.
 *
 * Language files live in /static/lang/<code>.json as flat {key: "text"} maps.
 * English (en.json) is the source of truth: every other language file must
 * contain the exact same key set (checked at build time, not at runtime --
 * if a key is missing at runtime we just fall back to English, then to the
 * raw key itself, so a missing translation never breaks the UI).
 *
 * Usage from Alpine:
 *   x-text="t('common_save')"
 *   :placeholder="t('login_username')"
 *
 * Adding a new language:
 *   1. Copy web/static/lang/en.json to web/static/lang/<code>.json
 *   2. Translate every value (keep the keys identical)
 *   3. Add {code:'Native Name'} to SUPPORTED_LANGS below
 *   That's it -- no other code changes needed, the settings language
 *   dropdown and the login-page switcher both read SUPPORTED_LANGS.
 */
window.SUPPORTED_LANGS = {
  en: 'English',
  tr: 'Türkçe',
  de: 'Deutsch',
  ru: 'Русский',
};

window.I18N = window.I18N || {};

window.dotserveLoadLang = async function (code) {
  if (window.I18N[code]) return window.I18N[code];
  try {
    const res = await fetch('/static/lang/' + code + '.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    window.I18N[code] = await res.json();
  } catch (e) {
    console.warn('[i18n] could not load language "' + code + '":', e);
    window.I18N[code] = {};
  }
  return window.I18N[code];
};

window.dotserveT = function (key) {
  const lang = window.dotserveCurrentLang || 'en';
  const dict = window.I18N[lang] || {};
  if (key in dict) return dict[key];
  const en = window.I18N.en || {};
  if (key in en) return en[key];
  return key;
};

/* Global `t()` (note: lowercase, no product prefix) so that Alpine
 * components OUTSIDE #dotserve-root -- e.g. the file picker, update modal,
 * and a few other standalone x-data scopes that live as siblings of the
 * root component rather than descendants of it -- can also resolve
 * x-text="t('some_key')" bindings. Alpine's expression evaluator checks
 * the component's own scope chain first, so this global is only ever used
 * as a fallback: rootApp()'s own reactive `t()` method (keyed off `this.lang`)
 * still takes precedence for anything actually nested inside #dotserve-root. */
window.t = window.dotserveT;

window.dotserveSetLang = async function (code) {
  if (!window.SUPPORTED_LANGS[code]) code = 'en';
  window.dotserveCurrentLang = code;
  try { localStorage.setItem('vp_lang', code); } catch (e) {}
  await window.dotserveLoadLang(code);
  if (code !== 'en') await window.dotserveLoadLang('en'); // fallback dict
};

window.dotserveInitLang = async function () {
  let saved = 'en';
  try { saved = localStorage.getItem('vp_lang') || 'en'; } catch (e) {}
  await window.dotserveSetLang(saved);
};

/* CRITICAL: Alpine (loaded with `defer`) auto-starts and evaluates every
 * x-text="t(...)" binding on the very first paint. Without this guard, that
 * first paint happens BEFORE the language JSON has finished loading (a race
 * condition), so any element that renders during that narrow window gets
 * stuck showing the raw key (e.g. "common_install") -- and because our
 * reactive `t()` only re-runs when Alpine's own `lang` property actually
 * changes value, a stale first render is never corrected on its own.
 *
 * `window.deferLoadingAlpine` is Alpine's official, documented hook for
 * exactly this situation: Alpine will not call Alpine.start() until we
 * invoke the callback it hands us. We use it to block Alpine's startup
 * until the language dictionary is fully loaded, so every x-text="t(...)"
 * binding -- on every page, nested or not -- gets a correct value on its
 * very first evaluation, with no race window at all.
 */
window.deferLoadingAlpine = function (callback) {
  window.dotserveInitLang().then(callback).catch(callback);
};
