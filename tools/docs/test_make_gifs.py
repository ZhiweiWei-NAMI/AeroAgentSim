"""Conversion command contract; real encoding is covered by the recording pipeline."""
import subprocess
from pathlib import Path

import pytest

from tools.docs import make_gifs


def test_segment_trims_speed_and_two_pass_palette() -> None:
    flow = make_gifs.Flow("00-overview", [make_gifs.Segment(Path("video.webm"), 4, 12)], 2)
    assert make_gifs.build_inputs(flow) == ["-ss", "4.000", "-t", "12.000", "-threads", "1", "-i", "video.webm"]
    first, second = make_gifs.build_filter_complex(2, 2, 12, 960, (1, 9))
    assert "palettegen=max_colors=48" in first and "paletteuse" in second
    assert "concat=n=2" in first and "scale=960" in second
    assert make_gifs.parse_overrides(["hero=2"], "speed") == {"hero": 2}
    with pytest.raises(SystemExit):
        make_gifs.parse_overrides(["hero=9:2"], "trim")


def test_oversized_gif_is_reported_without_lowering_quality(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    target = tmp_path / "flow.gif"
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[-1].endswith(".gif"):
            target.write_bytes(b"oversized")
        return subprocess.CompletedProcess(argv, 0, "", "")
    with pytest.raises(make_gifs.BudgetExceeded, match="--speed"):
        make_gifs.convert_flow("ffmpeg", make_gifs.Flow("flow", [make_gifs.Segment(tmp_path / "video.webm", 0, 1)]), target, 1, 12, 960, None, runner, tmp_path / "palette.png")
    assert len(calls) == 2
    assert "palettegen" in " ".join(calls[0])
    assert "paletteuse" in " ".join(calls[1])


def test_calibration_aligns_marks_to_the_actual_video_start() -> None:
    flow = make_gifs.Flow('04-photos', [make_gifs.Segment(Path('video.webm'), 8, 3)], black_prelude=True)
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert 'blackdetect' in ' '.join(argv)
        return subprocess.CompletedProcess(argv, 0, '', 'black_start:0.24 black_end:6.4 black_duration:6.16')
    make_gifs.align_prelude('ffmpeg', flow, runner)
    assert flow.segments[0].start == 8.24


@pytest.mark.parametrize(('seconds', 'valid'), [(14, False), (15, True), (27, True), (40, True), (41, False)])
def test_workbench_story_duration_budget(tmp_path: Path, seconds: int, valid: bool) -> None:
    target = tmp_path / 'flow.gif'
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[-1].endswith('.gif'):
            target.write_bytes(b'actual-encoded-output')
        return subprocess.CompletedProcess(argv, 0, '', f'time=00:00:{seconds:02d}.00')
    args = ('ffmpeg', make_gifs.Flow('flow', [make_gifs.Segment(tmp_path / 'video.webm', 0, seconds)]), target, 1000, 12, 960, None, runner, tmp_path / 'palette.png')
    if valid:
        assert make_gifs.convert_flow(*args).duration_s == seconds
    else:
        with pytest.raises(RuntimeError, match='15–40s'):
            make_gifs.convert_flow(*args)


def test_deleted_page_recordings_cannot_be_published(tmp_path: Path) -> None:
    videos = tmp_path / 'videos'; videos.mkdir()
    media = tmp_path / 'media'; media.mkdir()
    obsolete = media / '07-visualize.gif'; obsolete.write_bytes(b'previous-published-recording')
    video = videos / 'video.webm'; video.write_bytes(b'real-recording')
    import json
    (videos / 'recordings.json').write_text(json.dumps({'00-overview': {'segments': [{'path': str(video)}]}, '07-visualize': {'segments': [{'path': str(video)}]}}))
    with pytest.raises(SystemExit, match='all six workbench flows'):
        make_gifs.main(['--input', str(videos), '--output', str(tmp_path / 'media'), '--report', str(tmp_path / 'report.json'), '--ffmpeg', 'ffmpeg'], runner=lambda *args, **kwargs: subprocess.CompletedProcess([], 0, '', ''))
    assert obsolete.read_bytes() == b'previous-published-recording'


def test_missing_story_is_reported_without_substitute_footage(tmp_path: Path) -> None:
    import json
    videos = tmp_path / 'videos'; videos.mkdir()
    video = videos / 'video.webm'; video.write_bytes(b'recorded-source')
    (videos / 'recordings.json').write_text(json.dumps({'00-overview': {'segments': [{'path': str(video), 'duration': 20}]}}))
    media = tmp_path / 'media'; media.mkdir()
    report = tmp_path / 'report.json'
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[-1].endswith('.gif'):
            Path(argv[-1]).write_bytes(b'encoded-recording')
        return subprocess.CompletedProcess(argv, 0, '', 'time=00:00:20.00')
    with pytest.raises(SystemExit, match='Failed recordings:'):
        make_gifs.main(['--input', str(videos), '--output', str(media), '--report', str(report), '--ffmpeg', 'ffmpeg'], runner=runner)
    result = json.loads(report.read_text())
    assert [item['flow'] for item in result['items']] == ['00-overview']
    assert len(result['failures']) == 5
    assert not (media / '05-records-compare.gif').exists()
