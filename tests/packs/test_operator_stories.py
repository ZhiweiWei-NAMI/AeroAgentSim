"""Operator effects and recorded cross-callback lineage, using real kernel runs."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from aerokernel import Fact
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.packs.lowalt.tasks import LowaltTasks
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

SECOND = 10**9
CASES = [
    ('parcel-delivery', 'change-dropoff', {'task': 'task-1', 'destination': 'customer-2'}, 'lowalt.parcel.dropoff_changed'),
    ('medical-delivery', 'prioritize-medical', {'task': 'urgent-2', 'priority': 200}, 'lowalt.medical.prioritized'),
    ('powerline-inspection', 'report-defect', {'task': 'line-survey-2', 'site': 'line-end'}, 'lowalt.inspection.defect_reported'),
    ('crop-spraying', 'pause-spraying', {'task': 'field-1', 'duration_s': 20.0}, 'lowalt.spraying.paused'),
    ('air-taxi', 'close-vertiport', {'site': 'east-port'}, 'lowalt.site.closed'),
]


def prepared_story(identity: str, point: str, values: dict[str, Any], at: int = 10) -> tuple[dict[str, Any], Path]:
    prepared = TemplateRegistry().apply(identity, {'run_mode': 'interactive'})
    document = copy.deepcopy(prepared.document)
    assert document['ingress_streams'][0]['initial_watermark_ns'] == 0
    assert 'timeout_s' not in document['ingress_streams'][0]
    # A headless operator closes the source interval after submitting this input.
    document['ingress_streams'][0]['initial_watermark_ns'] = document['run']['until_ns']
    scenario = load_scenario(document, base=prepared.base)
    refs = {r.id: r for r in scenario.manifest.entities}
    payload = {k: {'$ref': refs[v].to_data()} if isinstance(v, str) else v for k, v in values.items()}
    document['bindings']['commands'].append({'schema': 'aas.runtime.inject_event', 'target': 'behaviour', 'at_ns': at * SECOND, 'payload': {'injection_point': point, 'payload': payload}})
    return document, prepared.base


def run(document: dict[str, Any], base: Path, seconds: int) -> tuple[list[dict[str, Any]], dict[tuple[str, str], Any]]:
    scenario = load_scenario(document, base=base)
    sim = Simulation(scenario)
    try:
        sim.start(); sim.run_until(seconds * SECOND)
        commits = [project(record) for record in sim.kernel.iter_records()]
        view = sim.kernel.view()
        facts = {(r.id, f.id): value.value for r in scenario.manifest.entities for f in scenario.registry.fields if scenario.registry.is_a(r.type_id, f.declaring_type) for value in [view.field((r, f.id), view.instant)] if isinstance(value, Fact)}
        return commits, facts
    finally:
        sim.close()


@pytest.mark.parametrize('identity,point,values,effect', CASES)
def test_typed_operator_story_has_real_effect_and_lineage(identity: str, point: str, values: dict[str, Any], effect: str) -> None:
    document, base = prepared_story(identity, point, values)
    commits, facts = run(document, base, 220 if identity != 'crop-spraying' else 100)
    messages = [m for c in commits for m in c['messages']]
    root = next(m for m in messages if m['schemaId'].startswith('lowalt.operator.'))
    response = next(m for m in messages if m['schemaId'] == effect)
    assert root['id'] in response['payload']['provenance']['message_ids']
    event_key = response['payload']['provenance']['event_key']
    assert any(event_key in m['payload'].get('provenance', {}).get('parent_event_keys', []) for m in messages)
    assert any(r['status'] == 'succeeded' for c in commits for r in c['receipts'])
    downstream = [m for m in messages if root['id'] in m['payload'].get('provenance', {}).get('message_ids', [])]
    if identity == 'parcel-delivery':
        assert facts['task-1', 'lowalt.task_destination'] == 'customer-2'
        assert any(m['schemaId'] == 'lowalt.task.completed' and m['payload']['task'] == 'task-1' for m in downstream)
    elif identity == 'medical-delivery':
        assert facts['urgent-2', 'lowalt.task_priority'] == 200
        assert facts['urgent-2', 'lowalt.task_created_ns'] == 10 * SECOND
        assert any(m['schemaId'] == 'lowalt.task.assigned' for m in downstream)
    elif identity == 'powerline-inspection':
        assert facts['line-survey-2', 'lowalt.task_created_ns'] == 10 * SECOND
        assert any(m['schemaId'] == 'lowalt.inspection.observed' for m in downstream)
    elif identity == 'crop-spraying':
        assert any(m['schemaId'] == 'lowalt.spraying.resumed' for m in downstream)
        assert not any(m['schemaId'] == 'lowalt.spraying.applied' and 10 * SECOND < int(m['at']['ns']) < 30 * SECOND for m in messages)
    else:
        assert facts['east-port', 'lowalt.site_open'] is False
        assert any(m['schemaId'] == 'lowalt.task.replanned' for m in downstream)
        assert all(value != 'east-port' for (entity, field), value in facts.items() if entity.startswith('flight-') and field in {'lowalt.task_origin','lowalt.task_destination'})


@pytest.mark.parametrize('identity,point,values,effect', CASES)
def test_targets_use_entity_contracts(identity: str, point: str, values: dict[str, Any], effect: str) -> None:
    prepared = TemplateRegistry().apply(identity)
    scenario = load_scenario(prepared.document, base=prepared.base)
    injection = next(p for package in prepared.document['behaviours'] for p in package.get('injection_points', []) if p['id'] == point)
    schema = scenario.registry.message(injection['emits']).schema
    refs = {r.id: r for r in scenario.manifest.entities}
    valid = {k: {'$ref': refs[v].to_data()} if isinstance(v, str) else v for k, v in values.items()}
    scenario.registry.validate(schema, valid)
    # Plain strings, missing generations and wrong target classes fail admission.
    for key, value in values.items():
        if isinstance(value, str):
            with pytest.raises(Exception):
                scenario.registry.validate(schema, values)
            missing_generation = copy.deepcopy(valid)
            del missing_generation[key]['$ref']['generation']
            with pytest.raises(Exception):
                scenario.registry.validate(schema, missing_generation)
            wrong_type = copy.deepcopy(valid)
            wrong_type[key] = {'$ref': next(r for r in refs.values() if scenario.registry.is_a(r.type_id, 'lowalt:UAV')).to_data()}
            with pytest.raises(Exception):
                scenario.registry.validate(schema, wrong_type)
            break


def test_food_delay_reaches_readiness_dispatch_and_delivery_without_changing_results(monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = TemplateRegistry().apply('food-delivery')
    document = copy.deepcopy(prepared.document)
    document['bindings']['commands'].append({'schema':'aas.runtime.inject_event','target':'behaviour','at_ns':10 * SECOND,'payload':{'injection_point':'restaurant_delay','payload':{'restaurant':'restaurant-east','delay_s':20.0}}})
    linked, facts = run(document, prepared.base, 360)
    messages = [m for c in linked for m in c['messages']]
    root = next(m for m in messages if m['schemaId'] == 'lowalt.operator.restaurant_delay')
    affected = next(m for m in messages if m['schemaId'] == 'lowalt.food.preparation_delayed')['payload']['tasks']
    delayed_key = next(m for m in messages if m['schemaId'] == 'lowalt.food.preparation_delayed')['payload']['provenance']['event_key']
    assert affected
    for task in affected:
        downstream = [m['schemaId'] for m in messages if m['payload'].get('task') == task and root['id'] in m['payload'].get('provenance',{}).get('message_ids',[])]
        assert {'lowalt.food.ready','lowalt.task.assigned','lowalt.task.picked_up','lowalt.food.delivered'} <= set(downstream)
        ready = next(m for m in messages if m['schemaId'] == 'lowalt.food.ready' and m['payload']['task'] == task)
        assert delayed_key in ready['payload']['provenance']['parent_event_keys']
    def no_lineage(self: LowaltTasks, ctx: Any, schema: str, payload: dict[str, Any], **kwargs: Any) -> None:
        ctx.emit(schema, payload, topic='lowalt.events')
    monkeypatch.setattr(LowaltTasks, '_business_event', no_lineage)
    plain, same_facts = run(document, prepared.base, 360)
    assert facts == same_facts
    for commit in linked:
        for m in commit['messages']:
            m['payload'].pop('provenance', None)
    assert linked == plain


def test_ineligible_medical_target_records_rejection_without_a_successful_business_effect() -> None:
    document, base = prepared_story('medical-delivery', 'prioritize-medical', {'task': 'routine-1', 'priority': 200})
    commits, facts = run(document, base, 20)
    messages = [m for c in commits for m in c['messages']]
    rejected = next(m for m in messages if m['schemaId'] == 'lowalt.operator.rejected')
    root = next(m for m in messages if m['schemaId'] == 'lowalt.operator.prioritize_medical')
    assert rejected['payload']['provenance']['message_ids'] == [root['id']]
    assert 'unfinished medical task' in rejected['payload']['reason']
    assert not any(m['schemaId'] == 'lowalt.medical.prioritized' for m in messages)
    assert facts['routine-1','lowalt.task_priority'] == 1


def test_live_investigation_replaces_the_scheduled_operator_input_and_keeps_lineage() -> None:
    prepared = TemplateRegistry().apply('security-patrol', {'run_mode': 'interactive'})
    compiled = load_scenario(prepared.document, base=prepared.base)
    assert not any(c['schema'] == 'aas.runtime.inject_event' for c in compiled.document['bindings']['commands'])
    document = copy.deepcopy(dict(compiled.document))
    document['ingress_streams'][0]['initial_watermark_ns'] = document['run']['until_ns']
    document['bindings']['commands'].append({'schema':'aas.runtime.inject_event','target':'behaviour','at_ns':10 * SECOND,'payload':{'injection_point':'investigate','payload':{'task':'investigation-1','origin':'home','destination':'incident-site'}}})
    commits, _ = run(document, prepared.base, 180)
    messages = [m for c in commits for m in c['messages']]
    root = next(m for m in messages if m['schemaId'] == 'lowalt.operator.investigate')
    assert sum(m['schemaId'] == 'lowalt.operator.investigate' for m in messages) == 1
    assert any(m['schemaId'] == 'lowalt.task.completed' and root['id'] in m['payload'].get('provenance',{}).get('message_ids',[]) for m in messages)
