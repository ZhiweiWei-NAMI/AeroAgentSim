# resource/__init__.py

from airfogsim.resource.airspace import AirspaceResource
from airfogsim.resource.frequency import FrequencyResource
from airfogsim.resource.landing import LandingResource

__all__ = [
    'AirspaceResource',
    'FrequencyResource',
    'LandingResource'
]