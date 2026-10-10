Resource Classes
================

Resources represent physical and logical entities that can be allocated and managed in simulations.

Base Resource
-------------

.. automodule:: aeroagentsim.core.resource
   :members:
   :undoc-members:
   :show-inheritance:

Landing Resources
-----------------

LandingResource
~~~~~~~~~~~~~~~

.. autoclass:: aeroagentsim.resource.landing.LandingResource
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Frequency Resources
-------------------

FrequencyResource
~~~~~~~~~~~~~~~~~

.. autoclass:: aeroagentsim.resource.frequency.FrequencyResource
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Resource Development Guide
--------------------------

Creating Custom Resources
~~~~~~~~~~~~~~~~~~~~~~~~~

To create a custom resource, inherit from the base Resource class:

.. code-block:: python

   from aeroagentsim.core.resource import Resource
   from aeroagentsim.core.enums import ResourceStatus

   class CustomResource(Resource):
       """Custom resource implementation."""

       def __init__(self, resource_id, custom_attribute, attributes=None):
           super().__init__(resource_id, attributes)
           self.custom_attribute = custom_attribute
           self.usage_count = 0

       def allocate_custom(self, user_id):
           """Custom allocation logic."""
           if self.status == ResourceStatus.AVAILABLE:
               self.current_allocations.add(user_id)
               self.usage_count += 1
               self.status = ResourceStatus.FULLY_ALLOCATED
               return True
           return False

       def release_custom(self, user_id):
           """Custom release logic."""
           if user_id in self.current_allocations:
               self.current_allocations.remove(user_id)
               self.usage_count -= 1
               self.status = ResourceStatus.AVAILABLE
               return True
           return False

Usage Examples
~~~~~~~~~~~~~~

Landing Resource
^^^^^^^^^^^^^^^^

.. code-block:: python

   from aeroagentsim.resource.landing import LandingResource

   # Create a landing resource
   landing_spot = LandingResource(
       resource_id="landing_001",
       location=(100, 200, 0),
       radius=15.0,
       max_capacity=2,
       has_charging=True,
       has_data_transfer=True
   )

   # Allocate to an agent
   success = landing_spot.allocate("drone_001")

   # Check capacity
   if landing_spot.has_capacity():
       print("Landing spot has available capacity")

   # Release allocation
   landing_spot.release("drone_001")

Frequency Resource
^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from aeroagentsim.resource.frequency import FrequencyResource

   # Create a frequency resource
   freq_block = FrequencyResource(
       resource_id="freq_2400_001",
       center_frequency=2400.0,  # MHz
       bandwidth=20.0,           # MHz
       max_users=1,
       power_limit=100.0         # mW
   )

   # Assign frequency to communication link
   success = freq_block.assign_to("transmitter_1", "receiver_1", 20.0)

   # Update channel conditions
   freq_block.update_channel_condition(
       noise_level=-95.0,
       interference=-80.0,
       sinr=15.0
   )

   # Release frequency
   freq_block.release("transmitter_1", "receiver_1")
