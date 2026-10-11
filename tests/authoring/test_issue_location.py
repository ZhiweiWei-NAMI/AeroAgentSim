from aeroagentsim.authoring.workspace import issue_location


def test_loader_labels_locate_run_fields_without_a_path_attribute():
    assert issue_location({'run': {'seed': 1.5}}, '$', 'run.seed: expected integer >= 0') == {
        'path': '/run/seed', 'section': 'run',
    }


def test_dotted_fact_ids_remain_one_pointer_token():
    scenario = {'entities': [{'id': 'restaurant', 'facts': {'lowalt.site_capacity': 0}}]}
    assert issue_location(scenario, '$.entities[0].facts.lowalt.site_capacity', 'invalid capacity') == {
        'path': '/entities/0/facts/lowalt.site_capacity', 'section': 'objects',
        'entity_id': 'restaurant', 'field': 'lowalt.site_capacity',
    }


def test_wire_pointers_preserve_escaped_field_ids_and_entity_identity():
    scenario = {'entities': [{'id': 'device', 'facts': {'capacity/a~b': 0}}]}
    assert issue_location(scenario, '/entities/0/facts/capacity~1a~0b', 'invalid capacity') == {
        'path': '/entities/0/facts/capacity~1a~0b', 'section': 'objects',
        'entity_id': 'device', 'field': 'capacity/a~b',
    }


def test_unlocated_errors_are_not_assigned_an_invented_field():
    assert issue_location({}, '$', 'The registry digest does not match') == {}
    assert issue_location({}, '$.chains.traffic.incident_report.trigger', 'invalid trigger') == {}
