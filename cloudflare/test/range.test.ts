import { describe, expect, it } from "vitest";

import { parseByteRange, RangeNotSatisfiable } from "../src/range";

describe("byte range parser", () => {
  it.each([
    { value: "bytes=0-0", expected: { start: 0, end: 0 } },
    { value: "bytes=4-8", expected: { start: 4, end: 8 } },
    { value: "bytes=6-", expected: { start: 6, end: 11 } },
    { value: "bytes=-3", expected: { start: 9, end: 11 } },
    { value: "bytes=4-99", expected: { start: 4, end: 11 } },
    { value: "bytes=-99", expected: { start: 0, end: 11 } },
  ])("parses and clamps $value", ({ value, expected }) => {
    expect(parseByteRange(value, 12)).toEqual(expected);
  });

  it.each([
    "items=0-1",
    "bytes=",
    "bytes=-",
    "bytes=a-2",
    "bytes=0-b",
    "bytes=+1-2",
    "bytes=1e1-",
    "bytes=1.5-2",
    "bytes=0-1,4-5",
    "bytes=-0",
    "bytes=8-7",
    "bytes=12-",
    "bytes=99-100",
    "bytes=9007199254740992-",
    "bytes=0-9007199254740992",
    "bytes=-9007199254740992",
  ])("rejects %s", (value) => {
    expect(() => parseByteRange(value, 12)).toThrow(RangeNotSatisfiable);
  });

  it.each([0, -1])("rejects an asset size of %s", (size) => {
    expect(() => parseByteRange("bytes=0-0", size)).toThrow(RangeNotSatisfiable);
  });
});
