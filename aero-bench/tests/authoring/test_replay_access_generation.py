from aero_bench.control.contracts import ReplayAccessRequest, ReplayAccessResponse
from tools import generate_contracts as generator


def test_replay_access_contracts_are_in_the_schema_and_validator_graphs():
    targets = {target.slug: target for target in generator._TARGETS}
    assert targets["replay-access-request"].factory() == ReplayAccessRequest.model_json_schema()
    assert targets["replay-access-response"].factory() == ReplayAccessResponse.model_json_schema()
    fields = generator.AeroBenchContractBundle.model_fields
    assert fields["replay_access_request"].annotation is ReplayAccessRequest
    assert fields["replay_access_response"].annotation is ReplayAccessResponse
    validators = generator._typescript_validators().decode()
    assert "export function assertReplayAccessRequest" in validators
    assert "export function assertReplayAccessResponse" in validators


def test_replay_access_openapi_matches_the_bootstrap_auth_and_read_only_route():
    schemas = {target.slug: generator._schema_document(target) for target in generator._TARGETS}
    api = generator._openapi_document(schemas)
    operation = api["paths"]["/v1/runs/{run_id}/replay-access"]["post"]
    assert operation["security"] == [{"bootstrapBearer": []}]
    assert {item["name"] for item in operation["parameters"]} == {"run_id", "X-Aero-Bench-CSRF", "Origin"}
    assert all(item["required"] for item in operation["parameters"])
    assert operation["requestBody"]["required"] is True
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReplayAccessRequest",
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReplayAccessResponse",
    }
    assert api["components"]["schemas"]["ReplayAccessResponse"]["properties"]["read_only"]["const"] is True
