import { describe, expect, it } from "vitest";
import { CameraState } from "./camera";

describe("CameraState", () => {
  it("emits one-shot focus requests", () => {
    const camera = new CameraState();
    const requests: string[] = [];
    camera.subscribeFocus((target) => {
      if (target !== null) {
        requests.push(`${target.kind}:${target.id}`);
      }
    });

    camera.focus({ kind: "entity", id: "uav.alpha" });
    camera.focus({ kind: "entity", id: "uav.alpha" });
    expect(requests).toEqual(["entity:uav.alpha", "entity:uav.alpha"]);
  });

  it("follows one target and releases", () => {
    const camera = new CameraState();
    const followed: string[] = [];
    camera.subscribeFollow((target) => {
      followed.push(target === null ? "null" : target.id);
    });

    const target = { kind: "entity" as const, id: "uav.alpha" };
    camera.follow(target);
    expect(camera.followed()).toEqual(target);
    expect(camera.isFollowing(target)).toBe(true);
    expect(camera.isFollowing({ kind: "entity", id: "ugv.beta" })).toBe(false);

    camera.releaseFollow();
    expect(camera.followed()).toBeNull();
    expect(camera.isFollowing(target)).toBe(false);
    expect(followed).toEqual(["null", "uav.alpha", "null"]);
  });
});
