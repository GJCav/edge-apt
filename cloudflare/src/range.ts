export interface ByteRange {
  start: number;
  end: number;
}

export class RangeNotSatisfiable extends Error {}

export function parseByteRange(value: string, size: number): ByteRange {
  if (!value.startsWith("bytes=") || value.includes(",") || size < 1) {
    throw new RangeNotSatisfiable();
  }
  const specification = value.slice("bytes=".length);
  const match = /^(\d*)-(\d*)$/.exec(specification);
  if (!match || (match[1] === "" && match[2] === "")) {
    throw new RangeNotSatisfiable();
  }
  if (match[1] === "") {
    const suffixLength = parseInteger(match[2]);
    if (suffixLength < 1) throw new RangeNotSatisfiable();
    return {
      start: Math.max(0, size - suffixLength),
      end: size - 1,
    };
  }
  const start = parseInteger(match[1]);
  if (start >= size) throw new RangeNotSatisfiable();
  const requestedEnd = match[2] === "" ? size - 1 : parseInteger(match[2]);
  if (requestedEnd < start) throw new RangeNotSatisfiable();
  return { start, end: Math.min(requestedEnd, size - 1) };
}

function parseInteger(value: string): number {
  if (!/^\d+$/.test(value)) throw new RangeNotSatisfiable();
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new RangeNotSatisfiable();
  return parsed;
}
