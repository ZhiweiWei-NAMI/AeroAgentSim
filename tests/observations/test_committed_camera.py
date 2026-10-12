"""The photographic state is the issued cut, independent of render duration."""
import time
from pathlib import Path
import pytest

from aerokernel import CommandRequest, Instant
from aeroagentsim.observations.committed import committed_scene
from tests.observations.test_capture import ACTOR, FixtureRenderer, png, setup_capture

HEADER = {'types': [{'typeId': 'Subject', 'ancestors': []}],
          'presentation': [{'typeId': 'Subject', 'positionField': 'pose'}]}


def test_snapshot_is_read_only_and_excludes_later_lifecycle(tmp_path: Path) -> None:
    kernel, capture, request = setup_capture(tmp_path, FixtureRenderer(png()))
    before = committed_scene(kernel.view()._store, request, HEADER)
    prefix = kernel.journal.bytes
    assert kernel.journal.bytes == prefix
    command = kernel.submit(CommandRequest('capture.request', 'capture', Instant(1), request.to_command_data()))
    kernel.run_until(1)
    assert kernel.view().action(command).status == 'succeeded'
    # The new capture entity is already committed, but was not alive at the
    # requested source cut. Reading that older cut must remain exact.
    assert committed_scene(kernel.view()._store, request, HEADER) == before
    assert before['source_cut'] == request.to_data()['source_cut']
    assert before['entities'][0]['key'] == {'id': ACTOR.id, 'generation': '0'}
    assert before['entities'][0]['fields'][0]['value'] == [3.0, 1.0, 2.0]
    assert before['entities'][0]['fields'][0]['producer'] == 'subject'
    kernel.close()


def test_capture_latency_cannot_change_committed_journal(tmp_path: Path) -> None:
    class Slow(FixtureRenderer):
        def render(self, request, **kwargs):
            time.sleep(0.04)
            return super().render(request, **kwargs)
    journals = []
    for name, renderer in [('fast', FixtureRenderer(png())), ('slow', Slow(png()))]:
        kernel, _, request = setup_capture(tmp_path / name, renderer)
        kernel.submit(CommandRequest('capture.request', 'capture', Instant(1), request.to_command_data()))
        kernel.run_until(2)
        journals.append(kernel.records[1:])
        kernel.close()
    assert journals[0] == journals[1]


def test_snapshot_uses_older_pose_when_newer_version_is_already_committed(tmp_path: Path, monkeypatch) -> None:
    from dataclasses import replace
    from tests.observations.test_capture import Subject
    original = Subject.bootstrap
    def bootstrap(self, ctx):
        original(self, ctx)
        ctx.wake_at(1, {'measure': True})
    def on_inputs(self, ctx):
        for dirty in ctx.dirty:
            if dirty.kind == 'timer':
                ctx.set(ACTOR, 'pose', [9.0, 8.0, 7.0])
    monkeypatch.setattr(Subject, 'bootstrap', bootstrap)
    monkeypatch.setattr(Subject, 'on_inputs', on_inputs)
    kernel, _, request = setup_capture(tmp_path, FixtureRenderer(png()))
    kernel.run_until(2)
    source = kernel.view()._store
    older = committed_scene(source, request, HEADER)
    newer = committed_scene(source, replace(request, source_cut=kernel.view().cut), HEADER)
    assert older['entities'][0]['fields'][0]['value'] == [3.0, 1.0, 2.0]
    assert newer['entities'][0]['fields'][0]['value'] == [9.0, 8.0, 7.0]
    kernel.close()


def test_snapshot_preserves_nanoseconds_above_javascript_integer_range(tmp_path: Path) -> None:
    from dataclasses import replace
    kernel, _, request = setup_capture(tmp_path, FixtureRenderer(png()))
    kernel.run_until(2**53 + 1)
    request = replace(request, source_cut=kernel.view().cut)
    snapshot = committed_scene(kernel.view()._store, request, HEADER)
    assert snapshot['source_cut']['instant'][0] == {'$integer': str(2**53 + 1)}
    assert snapshot['entities'][0]['fields'][0]['value'] == [3.0, 1.0, 2.0]
    kernel.close()


def test_headless_camera_requires_the_built_console(tmp_path: Path) -> None:
    from aeroagentsim.observations.committed import private_console

    # Without a console service, the camera serves the built console itself;
    # a missing build is reported, never replaced by another renderer.
    with pytest.raises(FileNotFoundError, match="npm run build"):
        private_console(tmp_path / "dist")
