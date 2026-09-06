/** Converts multiline persona editor fields into trimmed lists for the settings page. */
export function splitLines(value: string): string[] {
  return value
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter(Boolean);
}
