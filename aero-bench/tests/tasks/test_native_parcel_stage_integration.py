"""Independent synthetic stage-boundary probes; no flight evidence."""
import pytest

from aero_bench.tasks.logistics.native_parcel_runtime import NativeParcelRuntimeError
from tests.tasks import test_native_parcel_rpc as rpc
from tests.tasks import test_native_parcel_runtime as h


@pytest.fixture
def package():
    return h.lower_logistics_task_package(h._package_document())


@pytest.mark.parametrize("retained_pad", [0, 1])
def test_stage_itself_rejects_missing_pad_without_mutation(package, retained_pad):
    component = rpc.component(package)
    consumer = component.stage_consumer
    full = rpc.batch(package, 1)
    partial = full.model_copy(update={"observations": (full.observations[retained_pad],)})
    before_machine = component.machine.snapshot()
    before_consumer = consumer.snapshot()
    with pytest.raises(NativeParcelRuntimeError):
        consumer.consume_batch(partial)
    assert component.machine.snapshot() == before_machine
    assert consumer.snapshot() == before_consumer
    assert consumer.consume_batch(full) is True


def test_stage_rejects_two_different_carrier_sources_before_mutation(package):
    component = rpc.component(package)
    consumer = component.stage_consumer
    full = rpc.batch(package, 1)
    other = rpc.batch(package, 1, location=h._DROPOFF)
    bad = full.model_copy(update={"observations": (full.observations[0], other.observations[1])})
    before_machine = component.machine.snapshot()
    before_consumer = consumer.snapshot()
    with pytest.raises(NativeParcelRuntimeError):
        consumer.consume_batch(bad)
    assert component.machine.snapshot() == before_machine
    assert consumer.snapshot() == before_consumer
    assert consumer.consume_batch(full) is True
