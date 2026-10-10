from airfogsim.visualization.run_repository import RunRepository
from airfogsim.visualization.schemas import ConfigSnapshot


def make_snapshot(config_id: str = "config_default") -> ConfigSnapshot:
    return ConfigSnapshot(
        config_id=config_id,
        name="AeroAgentSim Test Config",
        coordinate_mode="simulation_plane",
        simulation_speed=1.0,
    )


def test_delete_completed_run_removes_directory_and_refreshes_latest_pointer(tmp_path):
    repository = RunRepository(base_dir=str(tmp_path / "runtime" / "aeroagentsim"))

    first_manifest = repository.create_run(make_snapshot("config_a"))
    repository.update_status("completed")

    second_manifest = repository.create_run(make_snapshot("config_b"))
    repository.update_status("completed")

    deleted = repository.delete_run(second_manifest.run_id)

    assert deleted.run_id == second_manifest.run_id
    assert not (repository.runs_dir / second_manifest.run_id).exists()
    assert repository.latest_pointer.read_text().strip() == first_manifest.run_id
    assert repository.active_run_id is None
    assert second_manifest.run_id not in repository._latest_spatial
    assert second_manifest.run_id not in repository._recent_logs


def test_delete_active_run_is_rejected(tmp_path):
    repository = RunRepository(base_dir=str(tmp_path / "runtime" / "aeroagentsim"))

    manifest = repository.create_run(make_snapshot())

    try:
        repository.delete_run(manifest.run_id)
        raised = None
    except RuntimeError as exc:
        raised = exc

    assert raised is not None
    assert "Active run cannot be deleted" in str(raised)
    assert (repository.runs_dir / manifest.run_id).exists()
