import type { ClientRequest, IncomingMessage } from "node:http";

export function forwardBrowserOrigin(
  proxyRequest: Pick<ClientRequest, "setHeader">,
  request: Pick<IncomingMessage, "headers">,
): void {
  if (request.headers.origin !== undefined || !request.headers.referer) return;
  let referer: URL;
  try {
    referer = new URL(request.headers.referer);
  } catch {
    return;
  }
  if (referer.protocol === "http:" || referer.protocol === "https:") {
    proxyRequest.setHeader("Origin", referer.origin);
  }
}
