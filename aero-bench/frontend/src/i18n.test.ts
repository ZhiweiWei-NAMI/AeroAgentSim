import { afterEach, describe, expect, it } from "vitest";
import {
  DEFAULT_LANGUAGE,
  currentLanguage,
  initLanguage,
  setLanguage,
  subscribeLanguage,
  t,
  tf,
  type Language,
} from "./i18n";

afterEach(() => {
  try {
    window.localStorage.clear();
  } catch {
    // Storage may be unavailable in some environments.
  }
  setLanguage(DEFAULT_LANGUAGE);
});

describe("viewer language", () => {
  it("defaults to Simplified Chinese", () => {
    expect(initLanguage()).toBe("zh");
    expect(currentLanguage()).toBe("zh");
    expect(t("section.overview")).toBe("任务总览");
  });

  it("switches to English and notifies subscribers", () => {
    initLanguage();
    const seen: Language[] = [];
    const unsubscribe = subscribeLanguage((language) => seen.push(language));

    setLanguage("en");
    expect(currentLanguage()).toBe("en");
    expect(t("section.overview")).toBe("Mission overview");
    expect(seen).toEqual(["en"]);

    unsubscribe();
    setLanguage("zh");
    expect(seen).toEqual(["en"]);
  });

  it("persists the chosen language and rejects unknown languages", () => {
    initLanguage();
    setLanguage("en");
    expect(window.localStorage.getItem("aero-bench.viewer.language")).toBe("en");
    expect(initLanguage()).toBe("en");

    setLanguage("fr" as Language);
    expect(currentLanguage()).toBe("en");
  });

  it("interpolates parameters without touching authoritative identifiers", () => {
    setLanguage("zh");
    expect(tf("aria.selectEntity", { kind: "无人机", id: "uav.alpha" })).toBe(
      "选择无人机实体 uav.alpha",
    );
    expect(tf("aria.eventAtTick", { n: 300 })).toBe("选择节拍 300 的公共事件");
  });
});
