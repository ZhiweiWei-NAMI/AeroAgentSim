"""Shared viewer camera fed by an exact, read-only committed state snapshot."""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from aerokernel.state import Fact, Store
from aerokernel.values import thaw

from .contracts import CaptureRequest, browser_encode
from .renderer import BrowserRenderer, RenderedFrame


FRONTEND_DIST = Path(__file__).resolve().parents[3] / 'frontend' / 'dist'
_PRIVATE_CONSOLE: dict[Path, tuple[str, threading.Thread]] = {}


def capture_url(console: str) -> str:
    console = console.rstrip('/')
    return console + '/?' + urlencode({
        'capture': '1', 'capture_assets': console + '/v1/studio/demo-capture-assets',
    })
_PRIVATE_LOCK = threading.Lock()


def private_console(frontend: Path) -> str:
    """Serve the built console for headless runs that have no console service.

    The camera page and its city assets come from the same app as `serve`, on a
    loopback port owned by this process; nothing is fabricated if it is missing.
    """
    frontend = frontend.resolve()
    with _PRIVATE_LOCK:
        cached = _PRIVATE_CONSOLE.get(frontend)
        # A console whose server thread has stopped is replaced, never reused.
        if cached is not None and cached[1].is_alive():
            return cached[0]
        if not (frontend / 'index.html').is_file():
            raise FileNotFoundError(
                f'camera needs the built console at {frontend} (cd frontend && npm run build)')
        import uvicorn

        from aeroagentsim.services.app import create_app

        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        root = Path(tempfile.mkdtemp(prefix='aas-camera-console-'))
        server = uvicorn.Server(uvicorn.Config(
            create_app(root / 'runs', frontend=frontend, studio_root=root / 'workspaces'),
            host='127.0.0.1', port=port, log_level='warning'))
        thread = threading.Thread(target=server.run, name='camera-console', daemon=True)
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started:
            if time.monotonic() > deadline or not thread.is_alive() or not threading.main_thread().is_alive():
                raise TimeoutError('private camera console did not start')
            time.sleep(0.05)
        _PRIVATE_CONSOLE[frontend] = (f'http://127.0.0.1:{port}', thread)
        return _PRIVATE_CONSOLE[frontend][0]


def committed_scene(source: Store, request: CaptureRequest, header: dict[str, Any]) -> dict[str, Any]:
    """Resolve lifecycle and fields at BOTH requested knowledge and validity cut.

    This is a state snapshot, never synthesized journal events. Later versions,
    including versions already committed when the render command dispatches,
    cannot enter the photograph. Only spatial objects need GPU resources.
    """
    from aeroagentsim.services.projector import instant, lossless, stamp

    source.check_cut(request.source_cut)
    types = {row['typeId']: row for row in header['types']}
    spatial = {row['typeId'] for row in header['presentation']}
    subject = request.camera.get('subject') if request.camera.get('revision') == 'uav-gimbal-oblique/v1' else None
    refs = {
        ref for ref, life in source.lives.items()
        if life.created.index <= request.source_cut.index
        and source.alive(ref, request.source_cut, request.source_cut.instant)
        and (ref.type_id in spatial or spatial.intersection(types[ref.type_id]['ancestors'])
             or (subject is not None and ref.id == subject['id'] and ref.generation == subject['generation']))
    }
    fields: dict[Any, list[dict[str, Any]]] = {ref: [] for ref in refs}
    for key in source.facts:
        if key[0] not in refs:
            continue
        fact = source.field(key, request.source_cut.instant, request.source_cut)
        if isinstance(fact, Fact):
            fields[key[0]].append({
                'fieldId': key[1], 'value': lossless(thaw(fact.value)),
                'producer': fact.producer, 'validFrom': instant(fact.valid.start),
                'validTo': instant(fact.valid.end) if fact.valid.end is not None else None,
                'available': instant(fact.available),
                'acquired': stamp(fact.acquired),
                'version': {'journalIndex': fact.version.record_index,
                            'itemOrdinal': fact.version.item_index},
            })
    return {
        'source_cut': browser_encode(request.to_data()['source_cut']),
        'entities': [
            {'key': {'id': ref.id, 'generation': str(ref.generation)},
             'typeId': ref.type_id, 'fields': fields[ref]}
            for ref in sorted(refs, key=lambda ref: (ref.id, ref.generation))
        ],
    }


class CommittedBrowserRenderer(BrowserRenderer):
    """The template camera uses the same built CaptureConsole as the workbench.

    Supplying an issued state snapshot also works for scheduled RunSessions;
    no service registration, stale feed index or full journal replay is needed.
    """
    def __init__(self, config: dict[str, Any], directory: Path) -> None:
        from aeroagentsim.authoring.demo import capture_manifest, city_assets
        from aeroagentsim.scenario.loader import contract

        spec = contract(config, 'capture.renderer',
                        {'mode', 'node_modules', 'timeout_s', 'asset_digest'},
                        {'browser_executable', 'viewer_url'})
        viewer = spec.get('viewer_url')
        # Without a console service the camera serves the built console itself,
        # started lazily on the first photograph (see _start).
        self.private = viewer is None and not os.environ.get('AEROAGENTSIM_CONSOLE_URL')
        if viewer is None:
            viewer = capture_url(os.environ.get('AEROAGENTSIM_CONSOLE_URL') or 'private:')
        super().__init__(viewer, node_modules=Path(spec['node_modules']),
                         timeout_s=spec['timeout_s'],
                         browser_executable=Path(spec['browser_executable']) if 'browser_executable' in spec else None,
                         service_run_id=directory.name)
        self.directory = directory
        self.asset_id = spec['asset_digest']
        # The legacy traffic asset ID denotes the template camera asset bundle.
        # Declare its actual shared-city inventory instead of silently accepting
        # a mismatched manifest in the browser. Request metadata stays authored.
        self.manifest = json.loads(capture_manifest())
        self.manifest['asset_id'] = self.asset_id
        _, inventory = city_assets()
        listed = {file['url'] for file in self.manifest['files']}
        self.manifest['files'].extend(
            {'url': '/v1/studio/demo-assets/' + name, 'asset_id': item['asset_id']}
            for name, item in inventory.items()
            if name.startswith('models/') and '/v1/studio/demo-assets/' + name not in listed
        )
        self.header: dict[str, Any] | None = None

    def _start(self):  # type: ignore[no-untyped-def]
        if self.private and self.process is None:
            self.viewer_url = capture_url(private_console(FRONTEND_DIST))
        return super()._start()

    def render_cut(self, request: CaptureRequest, source: Store) -> RenderedFrame:
        from aeroagentsim.services.projector import header
        if request.asset_digest != self.asset_id:
            raise ValueError('Capture request differs from configured camera asset set')
        if self.header is None:
            self.header = header(self.directory)
        scene = {
            'header': self.header,
            'configuration': {'service_run_id': self.directory.name,
                              'kernel_run_id': source.manifest.run_id,
                              'epoch': source.manifest.epoch},
            'snapshot': committed_scene(source, request, self.header),
            'asset_manifest': self.manifest,
        }
        return self.render(request, scene=scene)
