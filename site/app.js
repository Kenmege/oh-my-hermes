/*!
 * Oh My Hermes - site runtime.
 * Language switch, theme toggle, copy buttons, and the demo pause control.
 * No dependencies, no network calls.
 */
(function () {
  "use strict";

  var LANG_KEY = "omh.lang";
  var THEME_KEY = "omh.theme";
  var FALLBACK = "en";
  var currentLang = FALLBACK;

  function readStore(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (err) {
      return null;
    }
  }

  function writeStore(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (err) {
      /* storage unavailable (private mode); the choice still applies to this page */
    }
  }

  /* =============================================================== i18n */

  var I18N = window.OMH_I18N || { meta: {}, strings: {} };
  var SUPPORTED = Object.keys(I18N.meta);

  function normalize(tag) {
    if (!tag) return null;
    var lower = String(tag).toLowerCase();
    if (lower.indexOf("zh") === 0) return SUPPORTED.indexOf("zh") > -1 ? "zh" : null;
    var base = lower.split("-")[0];
    return SUPPORTED.indexOf(base) > -1 ? base : null;
  }

  // English by default. Localization is an explicit choice (?lang= or the
  // header switch), never inferred from the browser or OS locale.
  function initialLang() {
    var fromQuery = new URLSearchParams(window.location.search).get("lang");
    return normalize(fromQuery) || normalize(readStore(LANG_KEY)) || FALLBACK;
  }

  function translate(key, lang) {
    var entry = I18N.strings[key];
    if (!entry) return null;
    return entry[lang] != null ? entry[lang] : entry[FALLBACK];
  }

  function applyLang(lang) {
    currentLang = lang;
    var meta = I18N.meta[lang] || I18N.meta[FALLBACK];
    document.documentElement.lang = (meta && meta.htmlLang) || lang;

    document.querySelectorAll("[data-i18n]").forEach(function (node) {
      var value = translate(node.getAttribute("data-i18n"), lang);
      if (value != null) node.textContent = value;
    });

    // Trusted, author-owned markup from i18n.js only.
    document.querySelectorAll("[data-i18n-html]").forEach(function (node) {
      var value = translate(node.getAttribute("data-i18n-html"), lang);
      if (value != null) node.innerHTML = value;
    });

    // "attr:key" pairs, comma separated.
    document.querySelectorAll("[data-i18n-attr]").forEach(function (node) {
      node.getAttribute("data-i18n-attr").split(",").forEach(function (pair) {
        var parts = pair.split(":");
        if (parts.length !== 2) return;
        var value = translate(parts[1].trim(), lang);
        if (value != null) node.setAttribute(parts[0].trim(), value);
      });
    });

    var current = document.querySelector("[data-lang-current]");
    if (current && meta) current.textContent = meta.short;

    document.querySelectorAll(".langswitch__menu [data-lang]").forEach(function (button) {
      var option = button.closest("[role='option']");
      if (option) option.setAttribute("aria-selected", String(button.dataset.lang === lang));
    });

    syncDemoLabel();
    writeStore(LANG_KEY, lang);
  }

  function initLangSwitch() {
    var root = document.querySelector("[data-lang-switch]");
    if (!root) return;

    var button = root.querySelector(".langswitch__button");
    var menu = root.querySelector(".langswitch__menu");
    if (!button || !menu) return;

    function close() {
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
    }

    function open() {
      menu.hidden = false;
      button.setAttribute("aria-expanded", "true");
      var selected = menu.querySelector("[aria-selected='true'] button") || menu.querySelector("button");
      if (selected) selected.focus();
    }

    button.addEventListener("click", function (event) {
      event.stopPropagation();
      if (menu.hidden) open();
      else close();
    });

    menu.addEventListener("click", function (event) {
      var choice = event.target.closest("[data-lang]");
      if (!choice) return;
      applyLang(choice.dataset.lang);
      close();
      button.focus();
    });

    document.addEventListener("click", function (event) {
      if (!root.contains(event.target)) close();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || menu.hidden) return;
      close();
      button.focus();
    });

    menu.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      event.preventDefault();
      var items = Array.prototype.slice.call(menu.querySelectorAll("button"));
      var index = items.indexOf(document.activeElement);
      var next = event.key === "ArrowDown" ? index + 1 : index - 1;
      items[(next + items.length) % items.length].focus();
    });
  }

  /* ============================================================== theme */

  // With no saved choice the stylesheet follows prefers-color-scheme; the
  // toggle flips whatever is showing and remembers it.
  function shownTheme() {
    var set = document.documentElement.dataset.theme;
    if (set === "light" || set === "dark") return set;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }

  function initTheme() {
    var toggle = document.querySelector("[data-theme-toggle]");
    if (!toggle) return;
    toggle.addEventListener("click", function () {
      var next = shownTheme() === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      writeStore(THEME_KEY, next);
    });
  }

  /* =============================================================== copy */

  function initCopy() {
    document.querySelectorAll("[data-copy]").forEach(function (button) {
      button.addEventListener("click", function () {
        var source = document.querySelector(button.getAttribute("data-copy"));
        if (!source || !navigator.clipboard) return;
        navigator.clipboard.writeText(source.textContent.trim()).then(function () {
          button.setAttribute("data-copied", "");
          button.textContent = "✓";
          window.setTimeout(function () {
            button.removeAttribute("data-copied");
            button.textContent = translate("hero.copy", currentLang) || "Copy";
          }, 1600);
        });
      });
    });
  }

  /* =============================================================== demo */

  var demo = document.querySelector("[data-demo]");
  var demoToggle = document.querySelector("[data-demo-toggle]");

  function syncDemoLabel() {
    if (!demo || !demoToggle) return;
    var paused = demo.paused;
    demoToggle.textContent = translate(paused ? "dag.play" : "dag.pause", currentLang) || (paused ? "Play" : "Pause");
  }

  function initDemo() {
    if (!demo || !demoToggle) return;
    // Reduced motion: keep the poster (the finished graph) until asked.
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      demo.removeAttribute("autoplay");
      demo.pause();
    }
    demoToggle.addEventListener("click", function () {
      if (demo.paused) demo.play();
      else demo.pause();
    });
    demo.addEventListener("play", syncDemoLabel);
    demo.addEventListener("pause", syncDemoLabel);
    syncDemoLabel();
  }

  initDemo();
  initLangSwitch();
  initTheme();
  initCopy();
  applyLang(initialLang());
})();
