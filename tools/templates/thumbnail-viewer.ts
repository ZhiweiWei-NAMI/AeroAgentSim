/** Thumbnail entry point for the REAL console renderer, fed by its WAL projector. */
import { Viewport } from '../../frontend/src/viewport/viewport';
import { FeedStore } from '../../frontend/src/viewport/feed-store';
import type { FeedCommit, RunHeader } from '../../frontend/src/contracts/viewer-feed';

const host = document.getElementById('viewport')!;
(window as any).aeroCapture = {
  async render({ request }: any) {
    const response = await fetch('/feed.json');
    if (!response.ok) throw Error(`Replay feed HTTP ${response.status}`);
    const feed: { header: RunHeader; commits: FeedCommit[] } = await response.json();
    const store = new FeedStore(feed.header);
    for (const commit of feed.commits) store.ingest(commit);
    const cut = request.camera.snapshot.cut;
    const last = store.commits.at(-1);
    if (!last || last.commitIndex !== cut.index || last.at.ns !== String(cut.instant[0])) {
      throw Error('Thumbnail feed does not end at the requested committed cut');
    }
    store.seek(last.at.ns, last.commitIndex);
    feed.header.scene!.camera = { position: request.camera.overview.eye, target: request.camera.overview.target };
    const errors: string[] = [];
    const pending = new Map<string, number>();
    const viewport = new Viewport(host, feed.header, {
      quality: 'high', semantic: true, showAllLabels: false,
      onError: error => errors.push(String(error)),
      onStatus: status => {
        if (/unavailable|failed/i.test(status)) errors.push(status);
        const loading = /^Loading model: (.*?) ·/.exec(status);
        const loaded = /^Models loaded · (.*)$/.exec(status);
        if (loading) {
          const id = loading[1].replace(' (LOD1)', '');
          pending.set(id, (pending.get(id) ?? 0) + 1);
        }
        if (loaded) {
          const count = pending.get(loaded[1]);
          if (count === undefined) errors.push(`Unexpected model completion: ${loaded[1]}`);
          else if (count === 1) pending.delete(loaded[1]);
          else pending.set(loaded[1], count - 1);
        }
      },
    });
    viewport.setQuality('high', true);
    viewport.setDusk(request.camera.dusk);
    viewport.camera.fov = request.camera.fov;
    viewport.camera.updateProjectionMatrix();
    viewport.setSelection(request.camera.subject);
    try {
      const deadline = performance.now() + request.timeout_s * 1000;
      // Rendering creates the console's catalog batches; wait for real GLBs and
      // city completion, never accept a loading marker or a failed base scene.
      do {
        if (errors.length) throw Error(errors.join('\n'));
        viewport.setSelection(request.camera.subject);
        viewport.render(store, last.at.ns, 0, true);
        if (host.dataset.city === 'loaded' && pending.size === 0) break;
        if (performance.now() > deadline) throw Error(`Console assets timed out: ${[...pending]}`);
        await new Promise(resolve => setTimeout(resolve, 100));
      } while (true);
      await new Promise(resolve => requestAnimationFrame(resolve));
      viewport.setSelection(request.camera.subject);
      viewport.render(store, last.at.ns, 0, true);
      // Copy during the same task as rendering (the console canvas deliberately
      // does not preserve its drawing buffer). DOM labels / UI are excluded.
      const output = document.createElement('canvas');
      output.width = request.width; output.height = request.height;
      output.getContext('2d')!.drawImage(viewport.renderer.domElement, 0, 0, output.width, output.height);
      return { request, png_data_url: output.toDataURL('image/png') };
    } finally {
      viewport.dispose();
    }
  },
};
