"use strict";
// GPL-2.0-only. Shared policy is deliberately independent of privileged Electron APIs.
const APP_IDENTITY = "8K Lossless Video Editor";

function localOrigin(port) {
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error("Invalid backend port");
  return `http://127.0.0.1:${port}`;
}

function isOwnedURL(value, origin) {
  try {
    const url = new URL(value);
    return (
      url.protocol === "http:" &&
      url.hostname === "127.0.0.1" &&
      !url.username &&
      !url.password &&
      url.origin === origin
    );
  } catch {
    return false;
  }
}

function isSafeExternal(value) {
  if (
    typeof value !== "string" ||
    value.length > 2048 ||
    /[\u0000-\u0020\u007f]/.test(value)
  )
    return false;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    return (
      url.protocol === "https:" &&
      !url.username &&
      !url.password &&
      (!url.port || url.port === "443") &&
      host.includes(".") &&
      !/^[\d.]+$/.test(host) &&
      !host.includes(":") &&
      host !== "localhost" &&
      !host.endsWith(".localhost") &&
      !host.endsWith(".local") &&
      !host.endsWith(".internal")
    );
  } catch {
    return false;
  }
}

function isOwnedDownload(value, origin, initiator) {
  if (initiator !== origin) return false;
  try {
    const url = new URL(value);
    if (url.protocol === "blob:") return url.origin === origin;
    return (
      isOwnedURL(value, origin) &&
      /^\/api\/jobs\/[a-f0-9]{32}\/download$/.test(url.pathname) &&
      !url.search
    );
  } catch {
    return false;
  }
}

function assertOwnedStatus(status, token) {
  if (
    !status ||
    status.app !== APP_IDENTITY ||
    typeof token !== "string" ||
    !token ||
    status.desktop_instance_token !== token
  ) {
    throw new Error(
      "The local service did not match this desktop instance. No existing service was adopted.",
    );
  }
  return status;
}

function canGrantPermission(permission, requestingURL, origin) {
  return permission === "fullscreen" && isOwnedURL(requestingURL, origin);
}

module.exports = {
  APP_IDENTITY,
  localOrigin,
  isOwnedURL,
  isSafeExternal,
  isOwnedDownload,
  assertOwnedStatus,
  canGrantPermission,
};
