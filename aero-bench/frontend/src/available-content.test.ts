import { describe, expect, it } from "vitest";
import { showAvailableContent } from "./available-content";
import { element, emptyRow, keyValue } from "./ui";
import { t } from "./i18n";

describe("available replay fields", () => {
  it("removes absence but preserves real errors, zero and false", () => {
    const root = element("div");
    root.append(emptyRow("not recorded"), keyValue("voltage", t("label.undeclared")), keyValue("battery", "0"), keyValue("armed", "false"), keyValue("command", "failed"));
    showAvailableContent(root);
    expect(root.textContent).not.toContain("voltage");
    expect(root.textContent).not.toContain("not recorded");
    expect(root.textContent).toContain("battery0");
    expect(root.textContent).toContain("armedfalse");
    expect(root.textContent).toContain("commandfailed");
  });
});
