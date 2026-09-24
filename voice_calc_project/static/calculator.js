"use strict";
const shell = document.querySelector(".calculator-shell");
const expressionEl = document.getElementById("expression");
const resultEl = document.getElementById("result");
const statusEl = document.getElementById("status");
const csrf = document.querySelector('meta[name="csrf-token"]').content;
let expression = "";
let angleMode = "deg";
let inverse = false;
let calculating = false;
const MAX_LENGTH = 160;
const render = () => {
  expressionEl.textContent = (expression || "0").replaceAll("*", "×").replaceAll("/", "÷").replaceAll("pi", "π");
  expressionEl.scrollLeft = expressionEl.scrollWidth;
};
const message = (value) => { statusEl.textContent = value; };
function setAngleMode(mode) {
  angleMode = mode;
  document.getElementById("angle-btn").textContent = mode === "deg" ? "Deg" : "Rad";
  document.getElementById("mode-indicator").textContent = mode === "deg" ? "DEGREES" : "RADIANS";
}
function append(value) {
  const needsMultiply = /(?:[0-9)]|pi|e)$/.test(expression) &&
    (/^(?:sqrt|sin|cos|tan|asin|acos|atan|ln|log|fact)\($/.test(value) || value === "pi" || value === "e" || value === "(");
  const prefix = needsMultiply ? "*" : "";
  if (expression.length + prefix.length + value.length > MAX_LENGTH) { message("Expression is too long."); return; }
  expression += prefix + value;
  resultEl.textContent = "";
  message("");
  render();
}
function balanced(value) {
  const open = (value.match(/\(/g) || []).length;
  const close = (value.match(/\)/g) || []).length;
  return value + ")".repeat(Math.max(0, open - close));
}
function toBackend(value) {
  return balanced(value.replaceAll("π", "pi").replaceAll("×", "*").replaceAll("÷", "/").replaceAll("%", "/100"));
}
async function calculate() {
  if (!expression.trim() || calculating) return;
  calculating = true;
  message("Calculating…");
  const submitted = toBackend(expression);
  try {
    const response = await fetch(shell.dataset.calculateUrl, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ expression: submitted, angle_mode: angleMode })
    });
    if (response.redirected && response.url.includes("/login")) {
      message("Session expired. Sign in again.");
      return;
    }
    const data = await response.json();
    if (!response.ok || !data.success) {
      message(data.error || "Could not calculate.");
      return;
    }
    resultEl.textContent = "= " + data.result;
    expression = data.result;
    message("Saved to history");
    render();
  } catch (_) {
    message("Connection error. Try again.");
  } finally { calculating = false; }
}
document.querySelector(".keyboards").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.id === "science-toggle") {
    const advanced = document.getElementById("advanced-keys");
    advanced.hidden = !advanced.hidden;
    const expanded = !advanced.hidden;
    button.setAttribute("aria-expanded", String(expanded));
    button.setAttribute("aria-label", expanded ? "Hide advanced keys" : "Show advanced keys");
    shell.classList.toggle("science-open", expanded);
    return;
  }
  const action = button.dataset.action;
  if (action === "clear") { expression = ""; resultEl.textContent = ""; message("Cleared"); render(); }
  else if (action === "backspace") { expression = expression.slice(0, -1); resultEl.textContent = ""; render(); }
  else if (action === "equals") calculate();
  else if (action === "factorial") {
    let start = -1;
    const simple = expression.match(/(?:\d+(?:\.\d+)?|pi|e)$/);
    if (simple) start = expression.length - simple[0].length;
    else if (expression.endsWith(")")) {
      let depth = 0;
      for (let i = expression.length - 1; i >= 0; i--) {
        if (expression[i] === ")") depth++;
        else if (expression[i] === "(") {
          depth--;
          if (depth === 0) {
            start = i;
            while (start > 0 && /[a-z]/.test(expression[start - 1])) start--;
            break;
          }
        }
      }
    }
    if (start < 0) { message("Enter a number or group before !"); return; }
    const next = expression.slice(0, start) + "fact(" + expression.slice(start) + ")";
    if (next.length > MAX_LENGTH) { message("Expression is too long."); return; }
    expression = next; resultEl.textContent = ""; message(""); render();
  }
  else if (action === "angle") {
    setAngleMode(angleMode === "deg" ? "rad" : "deg");
  } else if (action === "inverse") {
    inverse = !inverse;
    button.setAttribute("aria-pressed", String(inverse));
    document.querySelectorAll("[data-inverse]").forEach((key) => {
      key.textContent = inverse ? key.dataset.inverse.slice(0, -1) : key.dataset.value.slice(0, -1);
    });
  } else if (action === "parenthesis") {
    const open = (expression.match(/\(/g) || []).length;
    const close = (expression.match(/\)/g) || []).length;
    append(open > close && /[0-9eπ)]$/.test(expression) ? ")" : "(");
  } else if (button.dataset.value) {
    append(inverse && button.dataset.inverse ? button.dataset.inverse : button.dataset.value);
  }
});
document.addEventListener("keydown", (event) => {
  if (event.target.closest("button, a, summary")) return;
  if (/^[0-9.+\-*/^()%]$/.test(event.key)) append(event.key);
  else if (event.key === "Enter" || event.key === "=") calculate();
  else if (event.key === "Backspace") { expression = expression.slice(0, -1); render(); }
  else if (event.key === "Escape") { expression = ""; resultEl.textContent = ""; render(); }
});
const themeSwitch = document.getElementById("theme-switch");
try { if (localStorage.getItem("voicecalc-theme") === "light") document.documentElement.dataset.theme = "light"; } catch (_) { /* storage disabled */ }
themeSwitch.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("voicecalc-theme", next); } catch (_) { /* storage disabled */ }
  document.querySelector(".menu").open = false;
});
const voiceButton = document.getElementById("voice-btn");
const voiceLabel = document.getElementById("voice-label");
voiceButton.addEventListener("click", () => {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) { message("Voice input is unavailable here. Use the keypad."); return; }
  const recognition = new SpeechRecognition();
  recognition.lang = navigator.language || "en-US";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.onstart = () => { voiceButton.classList.add("listening"); voiceLabel.textContent = "Listening…"; voiceButton.setAttribute("aria-label", "Listening"); message("Listening…"); };
  recognition.onend = () => { voiceButton.classList.remove("listening"); voiceLabel.textContent = "Tap to Speak"; voiceButton.setAttribute("aria-label", "Tap to Speak"); };
  recognition.onerror = () => { message("Voice input failed. Please try again."); };
  recognition.onresult = (event) => {
    const speech = event.results[0][0].transcript.trim();
    try {
      const parsed = window.VoiceCalcSpeech.parse(speech, angleMode);
      expression = parsed.expression;
      setAngleMode(parsed.angleMode);
      resultEl.textContent = "";
      render();
      calculate();
    } catch (error) {
      message(error instanceof Error ? error.message : "I could not understand the calculation.");
    }
  };
  try { recognition.start(); } catch (_) { message("Microphone could not start."); }
});
