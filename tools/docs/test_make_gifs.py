"""Conversion command contract; real encoding is covered by the recording pipeline."""
import subprocess
from pathlib import Path

import pytest

from tools.docs import make_gifs


def test_segment_trims_speed_and_two_pass_palette() -> None:
    flow = make_gifs.Flow("00-overview", [make_gifs.Segment(Path("video.webm"), 4, 12)], 2)
    assert make_gifs.build_inputs(flow) == ["-ss", "4.000", "-t", "12.000", "-threads", "1", "-i", "video.webm"]
    first, second = make_gifs.build_filter_complex(2, 2, 12, 960, (1, 9))
    assert "palettegen" in first and "paletteuse" in second
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
    flow = make_gifs.Flow('04-aerograph', [make_gifs.Segment(Path('video.webm'), 8, 3)], black_prelude=True)
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert 'blackdetect' in ' '.join(argv)
        return subprocess.CompletedProcess(argv, 0, '', 'black_start:0.24 black_end:6.4 black_duration:6.16')
    make_gifs.align_prelude('ffmpeg', flow, runner)
    assert flow.segments[0].start == 8.24
