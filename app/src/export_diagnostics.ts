// GPL-3.0-or-later; see LICENSE and LICENSE-EXCEPTION.

/** A short last meaningful line; the original diagnostic remains separate. */
export function failureSummary(details: string): string {
  const lines = details
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const meaningful = lines.filter(
    (line) =>
      !/^Traceback \(most recent call last\):$/.test(line) &&
      !/^File ".*", line \d+/.test(line) &&
      !/^\^+$/.test(line) &&
      !/^Conversion failed!?$/i.test(line),
  );
  const last = (
    meaningful.at(-1) ||
    lines.at(-1) ||
    "The renderer could not complete this export."
  ).replace(/^(?:ValueError|RuntimeError|OSError|Error):\s*/, "");
  return last.length > 240 ? `${last.slice(0, 239)}…` : last;
}

/** A rejected Clipboard API or unavailable legacy copy must never look successful. */
export async function copyDiagnostics(
  text: string,
  writeText: ((text: string) => Promise<void>) | undefined,
  legacyCopy: (text: string) => boolean,
): Promise<boolean> {
  if (writeText) {
    try {
      await writeText(text);
      return true;
    } catch {
      // Some browsers deny the asynchronous API but permit a user-gesture copy.
    }
  }
  try {
    return legacyCopy(text);
  } catch {
    return false;
  }
}
