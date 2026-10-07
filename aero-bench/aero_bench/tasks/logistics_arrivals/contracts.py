from typing import Literal

from aero_bench.config.models import Identifier, SchemaBoundFile, StrictModel

PACKAGE_ID = "logistics.arrivals.v1"
METRIC_ID = "logistics.scheduled-arrivals.complete"


class LogisticsArrivalsPackage(StrictModel):
    schema_version: Literal["aero-bench.logistics-arrivals-task/v1"]
    package_id: Literal["logistics.arrivals.v1"]
    task_id: Identifier
    verifier_id: Identifier
    provider_id: Identifier
    business_config: SchemaBoundFile


class LogisticsArrivalsVerifierConfig(StrictModel):
    schema_version: Literal["aero-bench.logistics-arrivals-verifier/v1"]
    package_id: Literal["logistics.arrivals.v1"]
