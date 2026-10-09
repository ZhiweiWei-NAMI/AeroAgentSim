/** Optional asynchronous WebGL timing. Unsupported/disjoint samples stay absent. */
interface TimerExtension { TIME_ELAPSED_EXT: number; GPU_DISJOINT_EXT: number }
export class GpuTimer {
  private extension: TimerExtension | null;
  private pending: WebGLQuery[] = [];
  private active: WebGLQuery | null = null;
  serial = 0;
  milliseconds?: number;
  constructor(private gl: WebGL2RenderingContext) {
    this.extension = gl.getExtension('EXT_disjoint_timer_query_webgl2');
  }
  get available() { return this.extension !== null; }
  begin() {
    const ext = this.extension;
    if (!ext) return;
    const disjoint = this.gl.getParameter(ext.GPU_DISJOINT_EXT) as boolean;
    if (disjoint) {
      for (const query of this.pending) this.gl.deleteQuery(query);
      this.pending.length = 0; this.milliseconds = undefined;
    } else {
      while (this.pending.length && this.gl.getQueryParameter(this.pending[0], this.gl.QUERY_RESULT_AVAILABLE)) {
        const query = this.pending.shift()!;
        this.milliseconds = Number(this.gl.getQueryParameter(query, this.gl.QUERY_RESULT)) / 1e6;
        this.serial++; this.gl.deleteQuery(query);
      }
    }
    // A busy GPU must not cause unbounded query allocation or a synchronous wait.
    if (this.pending.length >= 4) return;
    this.active = this.gl.createQuery();
    if (this.active) this.gl.beginQuery(ext.TIME_ELAPSED_EXT, this.active);
  }
  end() {
    if (!this.active || !this.extension) return;
    this.gl.endQuery(this.extension.TIME_ELAPSED_EXT);
    this.pending.push(this.active); this.active = null;
  }
  dispose() {
    for (const query of this.pending) this.gl.deleteQuery(query);
    this.pending.length = 0;
    if (this.active) { this.end(); this.dispose(); }
  }
}
