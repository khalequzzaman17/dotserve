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
window.I18N.en = {
  app_tagline: 'Server Management Panel',
  login_username: 'Username',
  login_password: 'Password',
  login_sign_in: 'Sign In',
  login_signing_in: 'Signing in...',
  login_enter_credentials: 'Enter username and password',
  login_2fa_title: 'Two-Factor Authentication',
  login_2fa_subtitle: 'Enter the 6-digit code from your authenticator app',
  login_verification_code: 'Verification code',
  login_verify: 'Verify',
  login_verifying: 'Verifying...',
  login_back_to_login: 'Back to login',
  nav_group_overview: 'Overview',
  nav_group_server: 'Server',
  nav_group_network: 'Network',
  nav_group_system: 'System',
  nav_dashboard: 'Dashboard',
  nav_websites: 'Websites',
  nav_wp: 'WP Toolkit',
  'nav_node-projects': 'Node.js Projects',
  'nav_go-projects': 'Go Projects',
  nav_databases: 'Databases',
  nav_files: 'File Manager',
  nav_services: 'Services',
  nav_modules: 'App Store',
  nav_docker: 'Docker',
  nav_firewall: 'Firewall',
  nav_terminal: 'Terminal',
  nav_backups: 'Backups',
  nav_mail: 'Mail Server',
  nav_ftp: 'FTP / SFTP',
  nav_cdn: 'CDN Manager',
  nav_cron: 'Cron Jobs',
  nav_monitoring: 'Monitoring',
  nav_logs: 'Log Viewer',
  nav_bandwidth: 'Bandwidth',
  nav_security: 'Security',
  nav_waf: 'WAF',
  nav_settings: 'Settings',
  common_administrator: 'Administrator',
  common_sign_out: 'Sign Out',
  common_live: 'Live',
  common_menu: 'Menu',
  common_module_not_installed: 'Module not installed',
  common_save: 'Save',
  common_cancel: 'Cancel',
  common_delete: 'Delete',
  common_refresh: 'Refresh',
  common_close: 'Close',
  common_restart: 'Restart',
  common_stop: 'Stop',
  common_start: 'Start',
  common_remove: 'Remove',
  common_upload: 'Upload',
  common_update: 'Update',
  common_search: 'Search',
  common_edit: 'Edit',
  common_create: 'Create',
  common_add: 'Add',
  common_install: 'Install',
  common_disable: 'Disable',
  common_confirm: 'Confirm',
  common_next: 'Next',
  common_download: 'Download',
  common_back: 'Back',
  settings_language: 'Language'
};

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
