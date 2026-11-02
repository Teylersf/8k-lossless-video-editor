/** A local-file seek must wait for finite metadata, including after remounts. */
export function finiteSeekTarget(
  requested: number,
  mediaDuration: number,
  readyState: number,
  currentTime: number,
): number | null {
  if (
    !Number.isFinite(requested) ||
    !Number.isFinite(mediaDuration) ||
    mediaDuration < 0 ||
    !Number.isFinite(readyState) ||
    readyState < 1
  ) {
    return null;
  }
  const target = Math.max(
    0,
    Math.min(requested, Math.max(0, mediaDuration - 0.001)),
  );
  return !Number.isFinite(currentTime) || Math.abs(currentTime - target) > 0.025
    ? target
    : null;
}
