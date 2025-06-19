Architecture Guide
==================

This guide provides a comprehensive overview of the AirFogSim architecture, design principles, and system organization.

System Overview
---------------

AirFogSim is built on a layered, event-driven architecture that promotes modularity, extensibility, and scalability. The system is designed around the following core principles:

* **Agent-Centric Design**: Autonomous agents are the primary actors
* **Component-Based Capabilities**: Functionality is provided through composable components
* **Event-Driven Communication**: Loose coupling through publish-subscribe patterns
* **Workflow Coordination**: High-level process management through state machines
* **Resource Management**: Dynamic allocation and contention modeling
* **Data Integration**: External data sources for realistic scenarios

Architecture Layers
-------------------

Core Layer
~~~~~~~~~~

The foundation layer provides essential abstractions and services:

.. code-block:: text

   ┌─────────────────────────────────────────────────────────────┐
   │                     Core Layer                              │
   ├─────────────────────────────────────────────────────────────┤
   │ Environment │ Agent  │ Component    │ Task  │ Workflow      │
   │ Resource    │ Trigger│ DataProvider │ Utils │ Enums │ Event │
   └─────────────────────────────────────────────────────────────┘

Key components:

* **Environment**: SimPy-based discrete event simulation engine
* **Agent**: Base class for autonomous entities
* **Component**: Base class for agent capabilities
* **Task**: Encapsulation of specific actions
* **Workflow**: State machine-based process coordination
* **Resource**: Base class for allocatable resources
* **DataProvider**: External data integration interface
* **Event System**: Publish-subscribe event management

Implementation Layer
~~~~~~~~~~~~~~~~~~~~

Concrete implementations of core abstractions:

.. code-block:: text

   ┌─────────────────────────────────────────────────────────────┐
   │                Implementation Layer                         │
   ├─────────────────────────────────────────────────────────────┤
   │ Agents      │ Components  │ Tasks      │ Workflows          │
   │ - Drone     │ - Mobility  │ - MoveTo   │ - Inspection       │
   │ - Terminal  │ - Sensing   │ - Compute  │ - Logistics        │
   │ - Station   │ - Charging  │ - Transfer │ - Charging         │
   └─────────────────────────────────────────────────────────────┘

Management Layer
~~~~~~~~~~~~~~~~

System-wide services and resource management:

.. code-block:: text

   ┌─────────────────────────────────────────────────────────────┐
   │                  Management Layer                           │
   ├─────────────────────────────────────────────────────────────┤
   │ AgentManager │ TaskManager │ WorkflowManager │TriggerManager│
   │ LandingManager │ FrequencyManager │ ContractManager         │
   └─────────────────────────────────────────────────────────────┘

Integration Layer
~~~~~~~~~~~~~~~~~

External interfaces and data sources:

.. code-block:: text

   ┌─────────────────────────────────────────────────────────────┐
   │                 Integration Layer                           │
   ├─────────────────────────────────────────────────────────────┤
   │ Weather │ Traffic │ Signal │ Statistics │ Visualization     │
   │ APIs    │ SUMO    │ RF     │ Collection │ Dashboard         │
   └─────────────────────────────────────────────────────────────┘

Core Design Patterns
--------------------

Agent-Component Pattern
~~~~~~~~~~~~~~~~~~~~~~~

Agents gain capabilities through composition of components:

.. code-block:: python

   class DroneAgent(Agent):
       def __init__(self, env, name, **kwargs):
           super().__init__(env, name, **kwargs)
           
           # Add capabilities through components
           self.add_component(MoveToComponent(env, self))
           self.add_component(SensorComponent(env, self))
           self.add_component(CommunicationComponent(env, self))

Benefits:
* **Modularity**: Components can be developed and tested independently
* **Reusability**: Components can be shared across agent types
* **Flexibility**: Agents can be configured with different capability sets
* **Maintainability**: Changes to capabilities don't affect agent core logic

Event-Driven Communication
~~~~~~~~~~~~~~~~~~~~~~~~~~

Loose coupling through publish-subscribe events:

.. code-block:: python

   # Publisher
   agent.trigger_event('battery_low', {'level': 15, 'agent_id': agent.id})
   
   # Subscriber
   agent.subscribe('weather_provider', 'weather_changed', 
                   agent._handle_weather_change)

Benefits:
* **Decoupling**: Publishers don't need to know about subscribers
* **Scalability**: Easy to add new event handlers
* **Flexibility**: Dynamic subscription and unsubscription
* **Debugging**: Centralized event logging and monitoring

State Machine Workflows
~~~~~~~~~~~~~~~~~~~~~~~~

Complex processes managed through state machines:

.. code-block:: python

   class InspectionWorkflow(Workflow):
       def _setup_transitions(self):
           sm = self.status_machine
           
           sm.add_transition('start', 'idle', 'moving',
                           task_suggestion={'task_class': 'MoveToTask'})
           
           sm.add_transition('arrive', 'moving', 'inspecting',
                           trigger=StateTrigger(self.owner, 'position'))

Benefits:
* **Clarity**: Clear representation of process states and transitions
* **Robustness**: Explicit handling of all possible states
* **Monitoring**: Easy to track process progress
* **Debugging**: Clear state history for troubleshooting

Resource Management Pattern
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Dynamic allocation and contention handling:

.. code-block:: python

   # Request resource
   landing_spot = env.landing_manager.request_resource(
       agent_id=drone.id,
       resource_type='landing_pad',
       location=(100, 100),
       duration=300
   )
   
   # Use resource
   if landing_spot:
       drone.execute_task('LandingTask', landing_spot=landing_spot)

Benefits:
* **Realism**: Models real-world resource constraints
* **Fairness**: Configurable allocation policies
* **Efficiency**: Optimal resource utilization
* **Monitoring**: Resource usage tracking and analytics

Data Flow Architecture
----------------------

The system follows a clear data flow pattern:

.. code-block:: text

   External Data → DataProviders → Events → Agents → Components → Tasks
                                     ↓
   Statistics ← Collectors ← Events ← State Changes ← Task Execution

1. **Data Ingestion**: DataProviders load external data
2. **Event Generation**: Data changes trigger simulation events
3. **Agent Decision**: Agents process events and make decisions
4. **Task Execution**: Components execute tasks based on agent decisions
5. **State Updates**: Task execution updates agent and resource states
6. **Event Propagation**: State changes trigger new events
7. **Data Collection**: Statistics collectors gather simulation data

Event System Architecture
-------------------------

The event system is central to system communication:

.. code-block:: text

   ┌─────────────────────────────────────────────────────────────┐
   │                    Event Registry                           │
   ├─────────────────────────────────────────────────────────────┤
   │  Source ID → Event Name → [Subscriber List]                 │
   │                                                             │
   │  Methods:                                                   │
   │  - register_event(source_id, event_name)                    │
   │  - subscribe(source_id, event_name, listener_id, callback)  │
   │  - trigger_event(source_id, event_name, data)               │
   │  - unsubscribe(source_id, event_name, listener_id)          │
   └─────────────────────────────────────────────────────────────┘

Event Types:
* **Agent Events**: State changes, task lifecycle
* **Component Events**: Performance metrics, errors
* **Workflow Events**: State machine transitions
* **System Events**: Resource allocation, environment updates
* **External Events**: Weather changes, traffic updates

State Management Architecture
----------------------------

Structured state management with validation:

.. code-block:: text

   ┌─────────────────────────────────────────────────────────────┐
   │                  Agent State System                         │
   ├─────────────────────────────────────────────────────────────┤
   │  State Templates (Class Level)                              │
   │  ├─ Key → {type, required, validator, description}          │
   │                                                             │
   │  Agent State (Instance Level)                               │
   │  ├─ Key → Value                                             │
   │                                                             │
   │  Composite State Access                                     │
   │  ├─ agent.state['battery_level']                            │
   │  ├─ agent.get_state('charging_station.status')              │
   └─────────────────────────────────────────────────────────────┘

Features:
* **Type Safety**: Automatic type validation
* **Required Fields**: Enforcement of required state attributes
* **Custom Validation**: User-defined validation functions
* **Composite Access**: Access to possessed object states
* **Change Tracking**: Automatic event triggering on state changes

Performance Considerations
-------------------------

The architecture is designed for performance and scalability:

Efficient Event Processing
~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Event Batching**: Multiple events processed together
* **Lazy Evaluation**: Events only processed when needed
* **Selective Subscription**: Agents only subscribe to relevant events
* **Event Filtering**: Early filtering of irrelevant events

Optimized State Management
~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Minimal State**: Only essential state is maintained
* **State Caching**: Frequently accessed state is cached
* **Lazy State Updates**: State only updated when changed
* **Efficient Validation**: Fast validation for common cases

Resource Optimization
~~~~~~~~~~~~~~~~~~~~~

* **Resource Pooling**: Reuse of common resources
* **Spatial Indexing**: Efficient spatial queries for location-based operations
* **Memory Management**: Automatic cleanup of unused resources
* **Parallel Processing**: Multi-threaded execution where possible

Extensibility Points
-------------------

The architecture provides multiple extension points:

Custom Agents
~~~~~~~~~~~~~

.. code-block:: python

   class CustomAgent(Agent):
       def _process_custom_logic(self):
           # Custom decision logic
           pass

Custom Components
~~~~~~~~~~~~~~~~~

.. code-block:: python

   class CustomComponent(Component):
       def _calculate_performance_metrics(self):
           # Custom performance calculation
           return {'custom_metric': value}

Custom Workflows
~~~~~~~~~~~~~~~~

.. code-block:: python

   class CustomWorkflow(Workflow):
       def _setup_transitions(self):
           # Custom state machine
           pass

Custom DataProviders
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class CustomDataProvider(DataProvider):
       def load_data(self):
           # Custom data loading
           pass

For detailed implementation guides, see the specific development guides for each component type.
