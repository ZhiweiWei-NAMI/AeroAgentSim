/** Monotonic simulation clock used by the renderer and replay controls. */
export interface PlaybackSample { readonly tick: number; readonly simTimeNs: number; }
export class PlaybackClock {
  private samples: readonly PlaybackSample[] = [];
  private positionNs = 0; private rate = 1; private running = false; private wallStartMs = 0; private simStartNs = 0;
  setSamples(samples: readonly PlaybackSample[]): void { this.samples = [...samples].sort((a,b)=>a.simTimeNs-b.simTimeNs); this.positionNs = this.samples[0]?.simTimeNs ?? 0; this.running = false; }
  setRate(rate:number):void { this.sync(); this.rate=Math.max(0,rate); }
  play(nowMs=performance.now()):void { this.sync(nowMs); this.running=true; this.wallStartMs=nowMs; this.simStartNs=this.positionNs; }
  pause(nowMs=performance.now()):void { this.sync(nowMs); this.running=false; }
  seekSimTime(simTimeNs:number):void { this.positionNs=this.clamp(simTimeNs); this.running=false; }
  isPlaying():boolean { return this.running; }
  simTimeNs(nowMs=performance.now()):number { this.sync(nowMs); return this.positionNs; }
  sampleBracket(nowMs=performance.now()):{before:PlaybackSample|null;after:PlaybackSample|null;alpha:number} { const time=this.simTimeNs(nowMs); if(!this.samples.length)return {before:null,after:null,alpha:0}; let i=0; while(i+1<this.samples.length&&this.samples[i+1]!.simTimeNs<=time)i++; const before=this.samples[i]!; const after=this.samples[i+1]??before; const span=after.simTimeNs-before.simTimeNs; return {before,after,alpha:span>0?Math.min(1,Math.max(0,(time-before.simTimeNs)/span)):0}; }
  private sync(nowMs=performance.now()):void { if(this.running)this.positionNs=this.clamp(this.simStartNs+(nowMs-this.wallStartMs)*1_000_000*this.rate); }
  private clamp(value:number):number { const first=this.samples[0]?.simTimeNs??value,last=this.samples.at(-1)?.simTimeNs??value; return Math.min(last,Math.max(first,value)); }
}
