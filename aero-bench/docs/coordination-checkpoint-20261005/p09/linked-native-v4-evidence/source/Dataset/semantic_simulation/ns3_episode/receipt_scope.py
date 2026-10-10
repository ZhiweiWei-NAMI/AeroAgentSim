"""Explicit owner/run/script binding for the supported five-stage replay.

This scope is deliberately not an adapter for unrelated authored event DAGs.
Epoch0 remains an explicit supported lifetime restriction, never a rank cast.
"""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ReceiptScope:
    episode_id: str
    scenario_id: str
    seed: int
    mission_owner: str
    gateway_owner: str
    script_ref: str
    events: tuple[str, ...]
    mission_epoch: int = 0
    gateway_epoch: int = 0

    def __post_init__(self):
        if self.scenario_id not in ('L6-2_v1','L6-2_v2'):
            raise ValueError('Only the source-reviewed L6-2 five-stage contracts are supported')
        if type(self.seed) is not int or self.seed not in (0,1,2):
            raise ValueError('Declared source seed must be0/1/2')
        if self.episode_id != f'{self.scenario_id}__seed{self.seed:02d}':
            raise ValueError('Episode/scenario/seed identity differs')
        if not all(isinstance(s,str) and s for s in (self.mission_owner,self.gateway_owner,self.script_ref)):
            raise ValueError('Explicit exact owners and source script required')
        if self.mission_owner == self.gateway_owner:
            raise ValueError('Mission and gateway are distinct bound physical owners')
        if type(self.mission_epoch) is not int or type(self.gateway_epoch) is not int or \
                (self.mission_epoch,self.gateway_epoch) != (0,0):
            raise ValueError('Current five-stage motion adapter explicitly supports one epoch0 per bound owner')
        if type(self.events) is not tuple or len(self.events) != 5 or len(set(self.events)) != 5:
            raise ValueError('Exact five distinct authored stage IDs required')
        if not all(isinstance(e,str) and e for e in self.events):
            raise ValueError('Event IDs must be complete nonempty strings')

    @property
    def telemetry_flow(self):
        return f'{self.episode_id}:c2_telemetry'

    @property
    def commanded(self):
        return (self.events[1],self.events[2],self.events[4])

    @property
    def action_names(self):
        return dict(zip(self.commanded,('slow_at_precheck','lock_backup_route','return_and_land')))

    def receipt(self):
        return {'schema_version':'p09.receipt.five-stage-binding/v1', **asdict(self)}

    @classmethod
    def from_dict(cls, row):
        required = {'schema_version','episode_id','scenario_id','seed','mission_owner','gateway_owner',
            'script_ref','events','mission_epoch','gateway_epoch'}
        if set(row) != required or row['schema_version'] != 'p09.receipt.five-stage-binding/v1':
            raise ValueError('Exact five-stage source binding schema required')
        fields = {k:v for k,v in row.items() if k != 'schema_version'}
        if type(fields['events']) is not list:
            raise ValueError('Serialized authored event IDs must be an array')
        fields['events'] = tuple(fields['events'])
        return cls(**fields)


DEFAULT_SCOPE = ReceiptScope('L6-2_v1__seed00','L6-2_v1',0,'uav_digital_l6_2_v1','tower_l6_2_v1',
    'Dataset/scenarios/L6_digital_layer/failure/L6-2_v1/event_script.json',
    ('c2_degradation','uav_delayed_response','backup_link_lock','nominal_c2_restore',
     'lifecycle_landing_uav_digital_l6_2_v1'))
