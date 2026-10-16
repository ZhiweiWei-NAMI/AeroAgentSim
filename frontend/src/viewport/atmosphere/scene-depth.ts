import * as T from 'three';

/** One depth blit per scene render, shared by every incident decal. No extra draw.
 * Sampling a separate attachment avoids a framebuffer feedback loop. This works
 * both inside the composer and in the draft's direct/default-framebuffer render.
 */
export class SceneDepth {
  private target?: T.WebGLRenderTarget;
  private frame = -1;
  private source: T.WebGLRenderTarget | null = null;
  private size = new T.Vector2();
  readonly texel = new T.Vector2();
  copy(renderer: T.WebGLRenderer): T.DepthTexture {
    const source = renderer.getRenderTarget();
    const frame = renderer.info.render.frame;
    if (this.target && this.frame === frame && this.source === source) return this.target.depthTexture!;
    this.frame = frame; this.source = source;
    const gl = renderer.getContext() as WebGL2RenderingContext;
    if (source) this.size.set(source.width, source.height); else renderer.getDrawingBufferSize(this.size);
    // Direct previews request stencil explicitly: ANGLE uses a packed depth /
    // stencil default attachment, whose internal format must match for a blit.
    const stencil = source ? source.stencilBuffer : gl.getContextAttributes()!.stencil;
    const type = source?.depthTexture?.type ?? (stencil ? T.UnsignedInt248Type : T.UnsignedIntType);
    const format = source?.depthTexture?.format ?? (stencil ? T.DepthStencilFormat : T.DepthFormat);
    if (!this.target || this.target.depthTexture!.type !== type || this.target.depthTexture!.format !== format) {
      this.target?.dispose();
      const depth = new T.DepthTexture(this.size.x, this.size.y, type);
      depth.format = format;
      this.target = new T.WebGLRenderTarget(this.size.x, this.size.y, {
        depthTexture: depth, stencilBuffer: format === T.DepthStencilFormat,
      });
    }
    this.target.setSize(this.size.x, this.size.y);
    this.texel.set(1 / this.size.x, 1 / this.size.y);
    const framebuffer = gl.getParameter(gl.FRAMEBUFFER_BINDING);
    renderer.setRenderTarget(this.target);
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, framebuffer);
    gl.blitFramebuffer(0, 0, this.size.x, this.size.y, 0, 0, this.size.x, this.size.y, gl.DEPTH_BUFFER_BIT, gl.NEAREST);
    // Restore both framebuffer bindings, viewport and Three's cached state.
    renderer.setRenderTarget(source);
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, framebuffer);
    return this.target.depthTexture!;
  }
  dispose() { this.target?.dispose(); }
}
