Core Framework
==============

The core framework provides the fundamental building blocks for AeroAgentSim simulations. These base classes define the architecture and interfaces that all other components build upon.

Environment
-----------

.. automodule:: aeroagentsim.core.environment
   :members:
   :undoc-members:
   :show-inheritance:

Agent
-----

The Agent class is the base class for all autonomous entities in the simulation.

.. autoclass:: aeroagentsim.core.agent.Agent
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Key Methods
~~~~~~~~~~~

State Management
^^^^^^^^^^^^^^^^

.. automethod:: aeroagentsim.core.agent.Agent.get_state
.. automethod:: aeroagentsim.core.agent.Agent.update_state
.. automethod:: aeroagentsim.core.agent.Agent.has_state
.. automethod:: aeroagentsim.core.agent.Agent.initialize_states

Component Management
^^^^^^^^^^^^^^^^^^^^

.. automethod:: aeroagentsim.core.agent.Agent.add_component
.. automethod:: aeroagentsim.core.agent.Agent.get_component
.. automethod:: aeroagentsim.core.agent.Agent.get_components

Task Management
^^^^^^^^^^^^^^^

.. automethod:: aeroagentsim.core.agent.Agent.execute_task
.. automethod:: aeroagentsim.core.agent.Agent.cancel_task

Event Handling
^^^^^^^^^^^^^^^

.. automethod:: aeroagentsim.core.agent.Agent.get_event
.. automethod:: aeroagentsim.core.agent.Agent.subscribe

Hook Methods
^^^^^^^^^^^^

These methods can be overridden by subclasses to customize behavior:

.. automethod:: aeroagentsim.core.agent.Agent._before_event_wait
.. automethod:: aeroagentsim.core.agent.Agent._check_agent_status
.. automethod:: aeroagentsim.core.agent.Agent.register_event_listeners

Component
---------

The Component class provides functional capabilities to agents.

.. autoclass:: aeroagentsim.core.component.Component
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Key Methods
~~~~~~~~~~~

Task Execution
^^^^^^^^^^^^^^^

.. automethod:: aeroagentsim.core.component.Component.execute_task
.. automethod:: aeroagentsim.core.component.Component.can_execute

Performance Metrics
^^^^^^^^^^^^^^^^^^^^

.. automethod:: aeroagentsim.core.component.Component._calculate_performance_metrics

State Monitoring
^^^^^^^^^^^^^^^^

.. automethod:: aeroagentsim.core.component.Component._on_agent_state_changed

Component Control
^^^^^^^^^^^^^^^^^^

.. automethod:: aeroagentsim.core.component.Component.disable
.. automethod:: aeroagentsim.core.component.Component.enable

Task
----

.. automodule:: aeroagentsim.core.task
   :members:
   :undoc-members:
   :show-inheritance:

Workflow
--------

The Workflow class coordinates high-level processes and goals.

.. autoclass:: aeroagentsim.core.workflow.Workflow
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Key Methods
~~~~~~~~~~~

Workflow Control
^^^^^^^^^^^^^^^^

.. automethod:: aeroagentsim.core.workflow.Workflow.start
.. automethod:: aeroagentsim.core.workflow.Workflow.reset

State Machine
^^^^^^^^^^^^^

.. automethod:: aeroagentsim.core.workflow.Workflow.is_active
.. automethod:: aeroagentsim.core.workflow.Workflow.get_current_suggested_task
.. automethod:: aeroagentsim.core.workflow.Workflow.update_status_from_state_machine

DataProvider
------------

The DataProvider class integrates external data sources into simulations.

.. autoclass:: aeroagentsim.core.dataprovider.DataProvider
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Key Methods
~~~~~~~~~~~

Data Integration
^^^^^^^^^^^^^^^^

.. automethod:: aeroagentsim.core.dataprovider.DataProvider.load_data
.. automethod:: aeroagentsim.core.dataprovider.DataProvider.start_event_triggering

Provider Control
^^^^^^^^^^^^^^^^

.. automethod:: aeroagentsim.core.dataprovider.DataProvider.block
.. automethod:: aeroagentsim.core.dataprovider.DataProvider.unblock

Event System
------------

.. automodule:: aeroagentsim.core.event
   :members:
   :undoc-members:
   :show-inheritance:

Trigger System
--------------

.. automodule:: aeroagentsim.core.trigger
   :members:
   :undoc-members:
   :show-inheritance:

Enumerations
------------

.. automodule:: aeroagentsim.core.enums
   :members:
   :undoc-members:
   :show-inheritance:

Utilities
---------

.. automodule:: aeroagentsim.core.utils
   :members:
   :undoc-members:
   :show-inheritance:

Resource Management
-------------------

.. automodule:: aeroagentsim.core.resource
   :members:
   :undoc-members:
   :show-inheritance:

LLM Integration
---------------

.. automodule:: aeroagentsim.core.llm_client
   :members:
   :undoc-members:
   :show-inheritance:
