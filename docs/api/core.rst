Core Framework
==============

The core framework provides the fundamental building blocks for AirFogSim simulations. These base classes define the architecture and interfaces that all other components build upon.

Environment
-----------

.. automodule:: airfogsim.core.environment
   :members:
   :undoc-members:
   :show-inheritance:

Agent
-----

The Agent class is the base class for all autonomous entities in the simulation.

.. autoclass:: airfogsim.core.agent.Agent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Key Methods
~~~~~~~~~~~

State Management
^^^^^^^^^^^^^^^^

.. automethod:: airfogsim.core.agent.Agent.get_state
.. automethod:: airfogsim.core.agent.Agent.update_state
.. automethod:: airfogsim.core.agent.Agent.has_state
.. automethod:: airfogsim.core.agent.Agent.initialize_states

Component Management
^^^^^^^^^^^^^^^^^^^^

.. automethod:: airfogsim.core.agent.Agent.add_component
.. automethod:: airfogsim.core.agent.Agent.get_component
.. automethod:: airfogsim.core.agent.Agent.get_components

Task Management
^^^^^^^^^^^^^^^

.. automethod:: airfogsim.core.agent.Agent.execute_task
.. automethod:: airfogsim.core.agent.Agent.cancel_task

Event Handling
^^^^^^^^^^^^^^^

.. automethod:: airfogsim.core.agent.Agent.get_event
.. automethod:: airfogsim.core.agent.Agent.subscribe

Hook Methods
^^^^^^^^^^^^

These methods can be overridden by subclasses to customize behavior:

.. automethod:: airfogsim.core.agent.Agent._before_event_wait
.. automethod:: airfogsim.core.agent.Agent._check_agent_status
.. automethod:: airfogsim.core.agent.Agent.register_event_listeners

Component
---------

The Component class provides functional capabilities to agents.

.. autoclass:: airfogsim.core.component.Component
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Key Methods
~~~~~~~~~~~

Task Execution
^^^^^^^^^^^^^^^

.. automethod:: airfogsim.core.component.Component.execute_task
.. automethod:: airfogsim.core.component.Component.can_execute

Performance Metrics
^^^^^^^^^^^^^^^^^^^^

.. automethod:: airfogsim.core.component.Component._calculate_performance_metrics

State Monitoring
^^^^^^^^^^^^^^^^

.. automethod:: airfogsim.core.component.Component._on_agent_state_changed

Component Control
^^^^^^^^^^^^^^^^^^

.. automethod:: airfogsim.core.component.Component.disable
.. automethod:: airfogsim.core.component.Component.enable

Task
----

.. automodule:: airfogsim.core.task
   :members:
   :undoc-members:
   :show-inheritance:

Workflow
--------

The Workflow class coordinates high-level processes and goals.

.. autoclass:: airfogsim.core.workflow.Workflow
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Key Methods
~~~~~~~~~~~

Workflow Control
^^^^^^^^^^^^^^^^

.. automethod:: airfogsim.core.workflow.Workflow.start
.. automethod:: airfogsim.core.workflow.Workflow.reset

State Machine
^^^^^^^^^^^^^

.. automethod:: airfogsim.core.workflow.Workflow.is_active
.. automethod:: airfogsim.core.workflow.Workflow.get_current_suggested_task
.. automethod:: airfogsim.core.workflow.Workflow.update_status_from_state_machine

DataProvider
------------

The DataProvider class integrates external data sources into simulations.

.. autoclass:: airfogsim.core.dataprovider.DataProvider
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Key Methods
~~~~~~~~~~~

Data Integration
^^^^^^^^^^^^^^^^

.. automethod:: airfogsim.core.dataprovider.DataProvider.load_data
.. automethod:: airfogsim.core.dataprovider.DataProvider.start_event_triggering

Provider Control
^^^^^^^^^^^^^^^^

.. automethod:: airfogsim.core.dataprovider.DataProvider.block
.. automethod:: airfogsim.core.dataprovider.DataProvider.unblock

Event System
------------

.. automodule:: airfogsim.core.event
   :members:
   :undoc-members:
   :show-inheritance:

Trigger System
--------------

.. automodule:: airfogsim.core.trigger
   :members:
   :undoc-members:
   :show-inheritance:

Enumerations
------------

.. automodule:: airfogsim.core.enums
   :members:
   :undoc-members:
   :show-inheritance:

Utilities
---------

.. automodule:: airfogsim.core.utils
   :members:
   :undoc-members:
   :show-inheritance:

Resource Management
-------------------

.. automodule:: airfogsim.core.resource
   :members:
   :undoc-members:
   :show-inheritance:

LLM Integration
---------------

.. automodule:: airfogsim.core.llm_client
   :members:
   :undoc-members:
   :show-inheritance:
