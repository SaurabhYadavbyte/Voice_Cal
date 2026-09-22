"use strict";
try {
  if (localStorage.getItem("voicecalc-theme") === "light") {
    document.documentElement.dataset.theme = "light";
  }
} catch (_) { /* Storage may be unavailable. */ }
