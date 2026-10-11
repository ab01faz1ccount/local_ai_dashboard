import { describe, it, expect } from "vitest";
import { countProblems, filterLogLines, lineLevel } from "../src/log-utils";

// real lines from a llama-server run
const INFO = "0.29.944.195 I slot launch_slot_: id  0 | task 5 | processing task, is_child = 0";
const WARN = "0.00.012.780 W srv  llama_server: this can be a security risk (cross-origin attacks)";
const ERR = "0.01.000.000 E srv  load_model: failed to load model";

describe("lineLevel", () => {
  it("reads the level letter of llama-server's own format", () => {
    expect(lineLevel(INFO)).toBe("info");
    expect(lineLevel(WARN)).toBe("warn");
    expect(lineLevel(ERR)).toBe("error");
    expect(lineLevel("0.00.001.000 D cmn something")).toBe("debug");
  });
  it("the level letter wins over words in the message (a warning mentioning 'error' stays a warning)", () => {
    expect(lineLevel("0.00.000.100 W srv some error was tolerated")).toBe("warn");
    expect(lineLevel("0.00.000.100 I srv load_model: failed? no, fine")).toBe("info");
  });
  it("unprefixed lines are judged by their words", () => {
    expect(lineLevel("Error: could not open file")).toBe("error");
    expect(lineLevel("warning: something odd")).toBe("warn");
    expect(lineLevel("just some output")).toBe("plain");
  });
  it("'I' inside a word is not a level", () => {
    expect(lineLevel("Initializing the thing")).toBe("plain");
  });
});

describe("countProblems", () => {
  it("counts warnings and errors separately", () => {
    expect(countProblems([INFO, WARN, WARN, ERR, "plain"])).toEqual({ warn: 2, error: 1 });
    expect(countProblems([])).toEqual({ warn: 0, error: 0 });
  });
});

describe("filterLogLines", () => {
  const lines = [INFO, WARN, ERR];
  it("no query -> everything, in order", () => {
    expect(filterLogLines(lines, "")).toEqual(lines);
    expect(filterLogLines(lines, "   ")).toEqual(lines);
  });
  it("case-insensitive substring", () => {
    expect(filterLogLines(lines, "SLOT")).toEqual([INFO]);
  });
  it("several words must ALL match (narrowing, not widening)", () => {
    expect(filterLogLines(lines, "srv failed")).toEqual([ERR]);
    expect(filterLogLines(lines, "srv slot")).toEqual([]);
  });
  it("only-problems keeps warnings and errors", () => {
    expect(filterLogLines(lines, "", true)).toEqual([WARN, ERR]);
    expect(filterLogLines(lines, "failed", true)).toEqual([ERR]);
    expect(filterLogLines(lines, "slot", true)).toEqual([]);
  });
  it("does not mutate its input", () => {
    const copy = [...lines];
    filterLogLines(lines, "slot", true);
    expect(lines).toEqual(copy);
  });
});
