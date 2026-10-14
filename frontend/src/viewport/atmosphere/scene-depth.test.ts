import { expect, it, vi } from 'vitest';
import * as T from 'three';
import { SceneDepth } from './scene-depth';

it.each([false, true])('blits matching depth once per render (default framebuffer: %s)', direct => {
  const source = direct ? null : new T.WebGLRenderTarget(960, 600, { depthTexture: new T.DepthTexture(960, 600, T.FloatType) });
  let current = source;
  const gl = { FRAMEBUFFER_BINDING: 1, READ_FRAMEBUFFER: 2, DEPTH_BUFFER_BIT: 4, NEAREST: 8,
    getContextAttributes: () => ({ stencil: true }), getParameter: () => source, bindFramebuffer: vi.fn(), blitFramebuffer: vi.fn() };
  const renderer = { info: { render: { frame: 1 } }, getRenderTarget: () => current, getContext: () => gl,
    getDrawingBufferSize: (out: T.Vector2) => out.set(960, 600), setRenderTarget: (target: T.WebGLRenderTarget | null) => { current = target; } } as unknown as T.WebGLRenderer;
  const depth = new SceneDepth(), texture = depth.copy(renderer);
  expect(texture.type).toBe(direct ? T.UnsignedInt248Type : T.FloatType);
  expect(texture.format).toBe(direct ? T.DepthStencilFormat : T.DepthFormat);
  expect(current).toBe(source); expect(depth.texel.toArray()).toEqual([1 / 960, 1 / 600]);
  expect(depth.copy(renderer)).toBe(texture); expect(gl.blitFramebuffer).toHaveBeenCalledTimes(1);
  renderer.info.render.frame++;
  expect(depth.copy(renderer)).toBe(texture); expect(gl.blitFramebuffer).toHaveBeenCalledTimes(2);
  depth.dispose(); source?.dispose();
});
