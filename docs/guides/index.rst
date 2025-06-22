Developer Guides
================

Comprehensive guides for extending and customizing AirFogSim.

.. toctree::
   :maxdepth: 2
   :caption: Development Guides:

   architecture
   agent_development
   component_development
   workflow_development
   dataprovider_development
   simulation_setup
   performance_tuning
   troubleshooting

Overview
--------

These guides provide in-depth information for developers who want to:

* Understand the AirFogSim architecture
* Create custom agent types
* Develop new component capabilities
* Design complex workflows
* Integrate external data sources
* Optimize simulation performance
* Debug and troubleshoot issues

Each guide includes:

* Conceptual explanations
* Step-by-step tutorials
* Code examples
* Best practices
* Common pitfalls and solutions

Getting Started with Development
--------------------------------

1. **Read the Architecture Guide**: Understand the overall system design
2. **Set up Development Environment**: Install development dependencies
3. **Explore Examples**: Study existing implementations
4. **Start Small**: Begin with simple customizations
5. **Test Thoroughly**: Use the testing framework
6. **Contribute Back**: Share your improvements with the community

Development Environment Setup
-----------------------------

.. code-block:: bash

   # Clone the repository
   git clone https://github.com/ZhiweiWei-NAMI/AirFogSim.git
   cd airfogsim
   
   # Install in development mode with all dependencies
   pip install -e ".[dev,docs]"
   
   # Run tests to verify setup
   pytest tests/
   
   # Build documentation
   cd docs
   make html
