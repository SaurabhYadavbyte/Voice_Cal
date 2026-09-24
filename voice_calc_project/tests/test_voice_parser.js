"use strict";

const assert = require("node:assert/strict");
const { parse } = require("../static/voice-parser.js");

const cases = [
  ["sine of thirty degrees", "sin(30)", "deg"],
  ["cosine pi radians", "cos(pi)", "rad"],
  ["square root of eighty one", "sqrt(81)", "deg"],
  ["five factorial", "fact(5)", "deg"],
  ["factorial of six", "fact(6)", "deg"],
  ["two to the power of eight", "2^8", "deg"],
  ["log base ten of one hundred", "log(100)", "deg"],
  ["natural log of e", "ln(e)", "deg"],
  ["inverse sine of one", "asin(1)", "deg"],
  ["sine thirty plus square root sixteen", "sin(30)+sqrt(16)", "deg"],
  ["open bracket two plus three close bracket times four", "(2+3)*4", "deg"],
  ["fifty percent plus ten", "(50/100)+10", "deg"],
  ["one hundred and twenty three point four five minus twenty", "123.45-20", "deg"],
  ["what is nine squared", "9^2", "deg"]
];

for (const [spoken, expected, mode] of cases) {
  assert.deepEqual(parse(spoken), { expression: expected, angleMode: mode }, spoken);
}
assert.deepEqual(parse("tangent one in radians", "deg"), { expression: "tan(1)", angleMode: "rad" });
assert.deepEqual(parse("sine ninety", "rad"), { expression: "sin(90)", angleMode: "rad" });

for (const invalid of ["", "delete my database", "sine", "two plus", "open bracket two plus three"]) {
  assert.throws(() => parse(invalid), undefined, invalid);
}
console.log("voice parser tests: OK");
