import assert from "node:assert/strict";
import test from "node:test";
import { instrumentMapSource } from "./capture-city-native-presentation-build.mjs";

test("private build exposes the existing map without changing its constructor", () => {
  const source = "this.map = new PublicTraceMap(this.root, options);";
  for (const name of ["__aeroStudioMap", "__aeroVisualMap"]) {
    const output = instrumentMapSource(source, name);
    assert(output.includes(`window.${name} = ${source}`));
    assert(output.includes('window.__aeroNativeCaptureBuild = "aero-bench.native-capture-build/v1"'));
    assert.equal(output.includes("window.__aeroCaptureApp = this;"), name === "__aeroVisualMap");
  }
});

test("private hooks reject absent, ambiguous, or unexpected map constructors", () => {
  assert.throws(() => instrumentMapSource("", "__aeroStudioMap"), /exactly once/);
  assert.throws(() => instrumentMapSource("this.map = new PublicTraceMap(this.map = new PublicTraceMap(",
    "__aeroStudioMap"), /exactly once/);
  assert.throws(() => instrumentMapSource("this.map = new PublicTraceMap(", "unexpected"), /Unknown/);
});
