Task Classes
============

Tasks encapsulate specific actions and operations that agents can perform through their components.

Base Task
---------

.. automodule:: airfogsim.core.task
   :members:
   :undoc-members:
   :show-inheritance:

Mobility Tasks
--------------

MoveToTask
~~~~~~~~~~

.. autoclass:: airfogsim.task.mobility.MoveToTask
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Computation Tasks
-----------------

FileComputeTask
~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.task.compute.FileComputeTask
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Data Collection Tasks
---------------------

FileCollectTask
~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.task.collect.FileCollectTask
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Transfer Tasks
--------------

FileTransferTask
~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.task.transfer.FileTransferTask
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Charging Tasks
--------------

RequestChargingStationTask
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autoclass:: airfogsim.task.charging.RequestChargingStationTask
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

ChargingTask
~~~~~~~~~~~~

.. autoclass:: airfogsim.task.charging.ChargingTask
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Logistics Tasks
---------------

PickupTask
~~~~~~~~~~

.. autoclass:: airfogsim.task.logistics.PickupTask
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

DeliveryTask
~~~~~~~~~~~~

.. autoclass:: airfogsim.task.logistics.DeliveryTask
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Task Development Guide
----------------------

Creating Custom Tasks
~~~~~~~~~~~~~~~~~~~~~

To create a custom task, inherit from the base Task class and implement the required methods:

.. code-block:: python

   from airfogsim.core.task import Task
   from airfogsim.core.enums import TaskStatus
   from typing import Dict, Any

   class CustomTask(Task):
       """Custom task implementation."""

       # Define required metrics from components
       NECESSARY_METRICS = ['custom_metric', 'processing_power']

       # Define states this task will update
       PRODUCED_STATES = ['custom_status', 'progress', 'result']

       def __init__(self, env, agent, component_name, task_name,
                    workflow_id=None, target_state=None, properties=None):
           super().__init__(env, agent, component_name, task_name,
                          workflow_id, target_state, properties)

           # Task-specific initialization
           self.custom_parameter = properties.get('custom_parameter', 0)
           self.progress = 0.0

       def _update_task_state(self, performance_metrics: Dict[str, Any]):
           """Update task progress based on component performance."""
           custom_metric = performance_metrics.get('custom_metric', 0)
           processing_power = performance_metrics.get('processing_power', 0)

           # Calculate progress based on metrics
           time_delta = self.env.now - self.last_update_time
           progress_increment = custom_metric * processing_power * time_delta / 1000

           self.progress = min(1.0, self.progress + progress_increment)

           # Update agent states
           self.agent.set_state('custom_status', 'processing')
           self.agent.set_state('progress', self.progress)

           # Check completion
           if self.progress >= 1.0:
               self.agent.set_state('result', 'completed')
               return True  # Task completed

           return False  # Task continues

       def _possessing_object_on_complete(self):
           """Handle possessing objects when task completes."""
           # Add result object to agent
           result_object = {'type': 'custom_result', 'value': self.custom_parameter}
           self.agent.add_possessing_object('custom_result', result_object)

       def _possessing_object_on_fail(self):
           """Handle possessing objects when task fails."""
           self.agent.set_state('custom_status', 'failed')

       def _possessing_object_on_cancel(self):
           """Handle possessing objects when task is cancelled."""
           self.agent.set_state('custom_status', 'cancelled')

Usage Examples
~~~~~~~~~~~~~~

Movement Task
^^^^^^^^^^^^^

.. code-block:: python

   from airfogsim.task.mobility import MoveToTask

   # Create a movement task
   move_task = MoveToTask(
       env=env,
       agent=drone_agent,
       component_name="MoveToComponent",
       task_name="move_to_target",
       target_state={'position': (100, 200, 50)},
       properties={'speed': 10.0}
   )

   # Execute through component
   component = drone_agent.get_component("MoveToComponent")
   component.execute_task(move_task)

File Processing Tasks
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from airfogsim.task.collect import FileCollectTask
   from airfogsim.task.compute import FileComputeTask
   from airfogsim.task.transfer import FileTransferTask

   # Collect data
   collect_task = FileCollectTask(
       env=env,
       agent=drone_agent,
       component_name="ImageSensingComponent",
       task_name="collect_image",
       properties={
           'file_name': 'sensor_data.jpg',
           'file_type': 'image',
           'file_size': 1024  # KB
       }
   )

   # Process collected data
   compute_task = FileComputeTask(
       env=env,
       agent=drone_agent,
       component_name="ComputationComponent",
       task_name="process_image",
       properties={
           'file_id': 'sensor_data.jpg',
           'result_file_name': 'processed_data.jpg'
       }
   )

   # Transfer results
   transfer_task = FileTransferTask(
       env=env,
       agent=drone_agent,
       component_name="CommunicationComponent",
       task_name="send_results",
       properties={
           'file_id': 'processed_data.jpg',
           'trans_target_agent_id': 'base_station_001'
       }
   )

Charging and Logistics
^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from airfogsim.task.charging import RequestChargingStationTask, ChargingTask
   from airfogsim.task.logistics import PickupTask, DeliveryTask

   # Request charging station
   request_task = RequestChargingStationTask(
       env=env,
       agent=drone_agent,
       component_name="ChargingComponent",
       task_name="request_charging",
       properties={'preferred_station_id': 'station_001'}
   )

   # Charge battery
   charge_task = ChargingTask(
       env=env,
       agent=drone_agent,
       component_name="ChargingComponent",
       task_name="charge_battery",
       target_state={'battery_level': 100.0},
       properties={'charging_efficiency': 0.95}
   )

   # Pickup cargo
   pickup_task = PickupTask(
       env=env,
       agent=delivery_drone,
       component_name="LogisticsComponent",
       task_name="pickup_package",
       properties={
           'pickup_location': (50, 100, 0),
           'package_id': 'package_001'
       }
   )

   # Deliver cargo
   delivery_task = DeliveryTask(
       env=env,
       agent=delivery_drone,
       component_name="LogisticsComponent",
       task_name="deliver_package",
       properties={
           'delivery_location': (200, 300, 0),
           'package_id': 'package_001'
       }
   )

Charging Tasks
--------------

.. automodule:: airfogsim.task.charging
   :members:
   :undoc-members:
   :show-inheritance:

Logistics Tasks
---------------

.. automodule:: airfogsim.task.logistics
   :members:
   :undoc-members:
   :show-inheritance:
