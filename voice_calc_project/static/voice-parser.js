"use strict";

(function attachVoiceParser(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.VoiceCalcSpeech = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  const SMALL = {
    zero: 0, oh: 0, one: 1, two: 2, three: 3, four: 4, five: 5,
    six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11,
    twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15,
    sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19
  };
  const TENS = {
    twenty: 20, thirty: 30, forty: 40, fifty: 50,
    sixty: 60, seventy: 70, eighty: 80, ninety: 90
  };
  const SCALES = { thousand: 1000, million: 1000000, billion: 1000000000 };
  const FUNCTIONS = new Set(["sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "ln", "log", "fact"]);
  const FILLER = new Set(["what", "is", "the", "answer", "calculate", "compute", "please", "of", "equals", "equal"]);

  const PHRASES = [
    [/\binverse\s+sine\b|\barc\s*sine\b|\barcsine\b/g, " asin "],
    [/\binverse\s+cosine\b|\barc\s*cosine\b|\barccosine\b/g, " acos "],
    [/\binverse\s+tangent\b|\barc\s*tangent\b|\barctangent\b/g, " atan "],
    [/\bsquare\s+root\b|\bunder\s+root\b/g, " sqrt "],
    [/\bnatural\s+logarithm\b|\bnatural\s+log\b/g, " ln "],
    [/\blogarithm\s+base\s+(?:ten|10)\b|\blog\s+base\s+(?:ten|10)\b|\bcommon\s+log(?:arithm)?\b/g, " log "],
    [/\bto\s+the\s+power\s+of\b|\braised\s+to\s+the\s+power\s+of\b|\braised\s+to\b|\bpower\s+of\b|\bpower\b/g, " ^ "],
    [/\bmultiplied\s+by\b|\bmultiply\s+by\b/g, " * "],
    [/\bdivided\s+by\b|\bdivide\s+by\b/g, " / "],
    [/\bopen\s+(?:parenthesis|parentheses|bracket)\b/g, " ( "],
    [/\bclose\s+(?:parenthesis|parentheses|bracket)\b/g, " ) "],
    [/\beuler(?:'s)?\s+(?:number|constant)\b/g, " e "],
    [/\bequal\s+to\b|\bequals\b/g, " "],
    [/\bsine\b/g, " sin "],
    [/\bcosine\b/g, " cos "],
    [/\btangent\b/g, " tan "],
    [/\blogarithm\b/g, " log "],
    [/\bpie\b/g, " pi "],
    [/\bplus\b|\badded\s+to\b|\badd\b/g, " + "],
    [/\bminus\b|\bsubtract\b/g, " - "],
    [/\btimes\b|\binto\b/g, " * "],
    [/\bover\b/g, " / "],
    [/\bsquared\b/g, " ^ 2 "],
    [/\bcubed\b/g, " ^ 3 "]
  ];

  function normalizeText(transcript) {
    let text = transcript.toLowerCase().replace(/[,’]/g, "'").replace(/,/g, "");
    for (const [pattern, replacement] of PHRASES) text = text.replace(pattern, replacement);
    return text.replace(/[^a-z0-9.+*/^()%'\s-]/g, " ").replace(/\s+/g, " ").trim();
  }

  function decimalDigits(words, start) {
    let digits = "";
    let index = start;
    while (index < words.length) {
      const word = words[index];
      if (/^\d+$/.test(word)) digits += word;
      else if (Object.prototype.hasOwnProperty.call(SMALL, word) && SMALL[word] < 10) digits += String(SMALL[word]);
      else break;
      index += 1;
    }
    if (!digits) throw new Error("Say digits after point.");
    return { digits, next: index };
  }

  function readNumber(words, start) {
    if (/^\d+(?:\.\d+)?$/.test(words[start] || "")) {
      let value = words[start];
      let next = start + 1;
      if (!value.includes(".") && words[next] === "point") {
        const decimal = decimalDigits(words, next + 1);
        value += "." + decimal.digits;
        next = decimal.next;
      }
      return { value, next };
    }

    let total = 0;
    let current = 0;
    let seen = false;
    let index = start;
    while (index < words.length) {
      const word = words[index];
      if (word === "and" && seen) {
        index += 1;
        continue;
      }
      if (Object.prototype.hasOwnProperty.call(SMALL, word)) {
        current += SMALL[word];
        seen = true;
      } else if (Object.prototype.hasOwnProperty.call(TENS, word)) {
        current += TENS[word];
        seen = true;
      } else if (word === "hundred" && seen) {
        current = Math.max(1, current) * 100;
      } else if (Object.prototype.hasOwnProperty.call(SCALES, word) && seen) {
        total += current * SCALES[word];
        current = 0;
      } else {
        break;
      }
      index += 1;
    }
    if (!seen) return null;

    let value = String(total + current);
    if (words[index] === "point") {
      const decimal = decimalDigits(words, index + 1);
      value += "." + decimal.digits;
      index = decimal.next;
    }
    return { value, next: index };
  }

  function tokenize(transcript) {
    const words = normalizeText(transcript).split(" ").filter(Boolean);
    const tokens = [];
    for (let index = 0; index < words.length;) {
      const word = words[index];
      const number = readNumber(words, index);
      if (number) {
        tokens.push(number.value);
        index = number.next;
        continue;
      }
      if (FILLER.has(word)) {
        index += 1;
        continue;
      }
      const mapped = {
        "×": "*", "÷": "/", x: "*", percent: "%", percentage: "%",
        factorial: "fact", pi: "pi", e: "e", point: "."
      }[word] || word;
      if (FUNCTIONS.has(mapped) || ["pi", "e", "+", "-", "*", "/", "^", "%", "(", ")"].includes(mapped)) {
        tokens.push(mapped);
        index += 1;
        continue;
      }
      if (mapped === ".") {
        tokens.push(".");
        index += 1;
        continue;
      }
      throw new Error("I did not understand “" + word + "”.");
    }
    return tokens;
  }

  function parseTokens(tokens) {
    let position = 0;
    const peek = () => tokens[position];
    const take = () => tokens[position++];

    function primary() {
      const token = take();
      if (!token) throw new Error("The calculation is incomplete.");
      if (/^\d+(?:\.\d+)?$/.test(token) || token === "pi" || token === "e") return token;
      if (token === ".") {
        const next = take();
        if (!/^\d+$/.test(next || "")) throw new Error("Say a number after point.");
        return "0." + next;
      }
      if (token === "(") {
        const value = addition();
        if (take() !== ")") throw new Error("Close the bracket in your calculation.");
        return "(" + value + ")";
      }
      if (FUNCTIONS.has(token)) return token + "(" + unary() + ")";
      throw new Error("The calculation is incomplete.");
    }

    function postfix() {
      let value = primary();
      while (peek() === "fact" || peek() === "%") {
        const operator = take();
        value = operator === "fact" ? "fact(" + value + ")" : "(" + value + "/100)";
      }
      return value;
    }

    function unary() {
      if (peek() === "+" || peek() === "-") return take() + unary();
      return postfix();
    }

    function power() {
      const left = unary();
      if (peek() !== "^") return left;
      take();
      return left + "^" + power();
    }

    function multiplication() {
      let value = power();
      while (peek() === "*" || peek() === "/") {
        const operator = take();
        value += operator + power();
      }
      return value;
    }

    function addition() {
      let value = multiplication();
      while (peek() === "+" || peek() === "-") {
        const operator = take();
        value += operator + multiplication();
      }
      return value;
    }

    const result = addition();
    if (position !== tokens.length) throw new Error("I could not understand the full calculation.");
    return result;
  }

  function parse(transcript, currentAngleMode = "deg") {
    if (typeof transcript !== "string" || !transcript.trim()) throw new Error("No calculation was heard.");
    if (transcript.length > 240) throw new Error("The spoken calculation is too long.");

    let angleMode = currentAngleMode;
    let text = transcript;
    if (/\b(?:in\s+)?radians?\b/i.test(text)) angleMode = "rad";
    else if (/\b(?:in\s+)?degrees?\b/i.test(text)) angleMode = "deg";
    text = text.replace(/\b(?:in\s+)?(?:degrees?|radians?)\b/gi, " ");

    const expression = parseTokens(tokenize(text));
    if (!expression || expression.length > 160 || !/^[0-9a-z+*/^().-]+$/.test(expression)) {
      throw new Error("I could not understand a safe calculation.");
    }
    return { expression, angleMode };
  }

  return { parse };
});
