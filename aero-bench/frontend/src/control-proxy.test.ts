// @vitest-environment node
import { expect, it, vi } from "vitest";
import { forwardBrowserOrigin } from "../scripts/control-proxy";

it.each(["http://127.0.0.1:5416", "http://localhost:5416", "https://other.example:9443"])(
  "forwards the actual Referer origin %s when Origin is absent",
  (origin) => {
    const setHeader = vi.fn();
    forwardBrowserOrigin({ setHeader }, { headers: { referer: `${origin}/?view=live` } });
    expect(setHeader).toHaveBeenCalledExactlyOnceWith("Origin", origin);
  },
);

it.each(["http://other.example:9000", "null", ""])("preserves explicit Origin %s", (origin) => {
  const setHeader = vi.fn();
  forwardBrowserOrigin({ setHeader }, { headers: { origin, referer: "http://localhost:5416/" } });
  expect(setHeader).not.toHaveBeenCalled();
});

it.each([undefined, "", "invalid URL", "data:text/plain,hello"])(
  "does not fabricate an Origin without an HTTP Referer: %s",
  (referer) => {
    const setHeader = vi.fn();
    forwardBrowserOrigin({ setHeader }, { headers: { referer } });
    expect(setHeader).not.toHaveBeenCalled();
  },
);
