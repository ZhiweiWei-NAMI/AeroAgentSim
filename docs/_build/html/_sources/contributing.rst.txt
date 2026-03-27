Contributing to AeroAgentSim
============================

We welcome contributions of all kinds! This document provides guidelines for contributing to AeroAgentSim.

.. contents:: Table of Contents
   :local:
   :depth: 2

Code of Conduct
---------------

By participating in this project, you agree to abide by our Code of Conduct. We are committed to providing a welcoming and inclusive environment for all contributors.

Our Standards
~~~~~~~~~~~~~

- Use welcoming and inclusive language
- Be respectful of differing viewpoints and experiences
- Gracefully accept constructive criticism
- Focus on what is best for the community
- Show empathy towards other community members

Getting Started
---------------

Prerequisites
~~~~~~~~~~~~~

Before contributing, please ensure you have:

- Python 3.8+ installed
- Git installed and configured
- Familiarity with discrete-event simulation concepts
- Basic understanding of UAV/fog computing systems (helpful but not required)

Development Installation
~~~~~~~~~~~~~~~~~~~~~~~~

1. Fork the repository on GitHub
2. Clone your fork locally:

   .. code-block:: bash

      git clone https://github.com/YOUR_USERNAME/AeroAgentSim.git
      cd AeroAgentSim

3. Set up development environment:

   .. code-block:: bash

      python -m venv aeroagentsim_dev
      source aeroagentsim_dev/bin/activate  # On Windows: aeroagentsim_dev\Scripts\activate
      pip install -e .[dev]

4. Add the upstream repository:

   .. code-block:: bash

      git remote add upstream https://github.com/ZhiweiWei-NAMI/AeroAgentSim.git

How to Contribute
-----------------

Types of Contributions
~~~~~~~~~~~~~~~~~~~~~~

We welcome several types of contributions:

🐛 **Bug Reports**
   - Use the GitHub issue tracker
   - Include detailed reproduction steps
   - Provide system information (OS, Python version, etc.)
   - Include relevant error messages and logs

💡 **Feature Requests**
   - Describe the feature and its use case
   - Explain why it would be valuable to the community
   - Consider providing a design proposal

📝 **Documentation Improvements**
   - Fix typos, clarify explanations
   - Add examples and tutorials
   - Improve API documentation
   - Translate documentation

🔧 **Code Contributions**
   - Bug fixes
   - New features
   - Performance improvements
   - Code refactoring

🧪 **Testing**
   - Add test cases for existing functionality
   - Improve test coverage
   - Add integration tests

Before You Start
~~~~~~~~~~~~~~~~

1. **Check existing issues** to avoid duplicate work
2. **Use our helper tools** to understand the codebase:

   .. code-block:: bash

      # Check existing classes before creating new ones
      python -m aeroagentsim.helper.class_finder --all
      
      # Find compatible components
      python -m aeroagentsim.helper.class_finder --find-component speed,processing_power

3. **Discuss major changes** by opening an issue first

Development Setup
-----------------

Development Tools
~~~~~~~~~~~~~~~~~

Install additional development tools:

.. code-block:: bash

   # Code formatting and linting
   pip install black flake8 isort mypy

   # Pre-commit hooks (optional but recommended)
   pip install pre-commit
   pre-commit install

Project Structure
~~~~~~~~~~~~~~~~~

.. code-block:: text

   project-root/
   ├── src/aeroagentsim/       # Main package
   │   ├── core/               # Core classes and interfaces
   │   ├── agent/              # Agent implementations
   │   ├── component/          # Component implementations
   │   ├── task/               # Task implementations
   │   ├── workflow/           # Workflow implementations
   │   ├── manager/            # Resource managers
   │   ├── examples/           # Example programs
   │   └── helper/             # Development tools
   ├── tests/                  # Test suite
   ├── frontend/               # Visualization interface
   └── docs/                   # Documentation

Coding Standards
----------------

Python Style Guide
~~~~~~~~~~~~~~~~~~

We follow PEP 8 with some modifications:

- **Line length**: 127 characters (to match GitHub's display width)
- **Import organization**: Use isort for consistent import ordering
- **Code formatting**: Use black for automatic formatting

.. code-block:: bash

   # Format code
   black src/ tests/

   # Sort imports
   isort src/ tests/

   # Check style
   flake8 src/ tests/

Naming Conventions
~~~~~~~~~~~~~~~~~~

- **Classes**: PascalCase (e.g., ``DroneAgent``, ``MoveToComponent``)
- **Functions/Methods**: snake_case (e.g., ``create_agent``, ``update_state``)
- **Constants**: UPPER_SNAKE_CASE (e.g., ``MAX_BATTERY_LEVEL``)
- **Files**: snake_case (e.g., ``drone_agent.py``)

Documentation Strings
~~~~~~~~~~~~~~~~~~~~~~

Use Google-style docstrings:

.. code-block:: python

   def create_agent(self, agent_class, agent_id, **kwargs):
       """Create a new agent in the environment.
       
       Args:
           agent_class: The class of agent to create.
           agent_id: Unique identifier for the agent.
           **kwargs: Additional arguments for agent initialization.
           
       Returns:
           The created agent instance.
           
       Raises:
           ValueError: If agent_id already exists.
       """

Testing Guidelines
------------------

Writing Tests
~~~~~~~~~~~~~

- **Unit tests**: Test individual components in isolation
- **Integration tests**: Test component interactions
- **Example tests**: Ensure examples run without errors

Test Structure
~~~~~~~~~~~~~~

.. code-block:: python

   import pytest
   from aeroagentsim.core.environment import Environment

   class TestYourFeature:
       """Test cases for your feature."""
       
       def test_basic_functionality(self):
           """Test basic functionality."""
           # Arrange
           env = Environment()
           
           # Act
           result = env.some_method()
           
           # Assert
           assert result is not None

Running Tests
~~~~~~~~~~~~~

.. code-block:: bash

   # Run all tests
   pytest tests/ -v

   # Run specific test file
   pytest tests/test_core/test_environment.py -v

   # Run with coverage
   pytest tests/ --cov=aeroagentsim --cov-report=html

   # Run example tests
   python -m aeroagentsim.examples.example_workflow_inspection
   python -m aeroagentsim.examples.example_workflow_logistics

Test Coverage
~~~~~~~~~~~~~

- Aim for >80% test coverage for new code
- Include both positive and negative test cases
- Test edge cases and error conditions

Documentation
-------------

Types of Documentation
~~~~~~~~~~~~~~~~~~~~~~

1. **API Documentation**: Docstrings in code
2. **User Guides**: Step-by-step tutorials
3. **Examples**: Working code samples
4. **Architecture Docs**: System design explanations

Writing Documentation
~~~~~~~~~~~~~~~~~~~~~

- Use clear, concise language
- Include code examples where appropriate
- Keep documentation up-to-date with code changes
- Use English for all documentation

For detailed documentation guidelines, see the main `CONTRIBUTING.md <https://github.com/ZhiweiWei-NAMI/AeroAgentSim/blob/main/CONTRIBUTING.md>`_ file.

Submitting Changes
------------------

Pull Request Process
~~~~~~~~~~~~~~~~~~~~

1. **Create a feature branch**:

   .. code-block:: bash

      git checkout -b feature/your-feature-name

2. **Make your changes**:
   - Follow coding standards
   - Add tests for new functionality
   - Update documentation as needed

3. **Test your changes**:

   .. code-block:: bash

      # Run tests
      pytest tests/ -v
      
      # Run examples
      python -m aeroagentsim.examples.example_workflow_inspection
      python -m aeroagentsim.examples.example_workflow_logistics
      
      # Check code style
      flake8 src/ tests/
      black --check src/ tests/

4. **Commit your changes**:

   .. code-block:: bash

      git add .
      git commit -m "Add feature: brief description"

5. **Push to your fork**:

   .. code-block:: bash

      git push origin feature/your-feature-name

6. **Create a Pull Request**:
   - Use a descriptive title
   - Explain what changes you made and why
   - Reference any related issues
   - Include screenshots for UI changes

Pull Request Guidelines
~~~~~~~~~~~~~~~~~~~~~~~

- **One feature per PR**: Keep changes focused and atomic
- **Clear description**: Explain the problem and solution
- **Tests included**: Add tests for new functionality
- **Documentation updated**: Update docs for user-facing changes
- **No breaking changes**: Unless discussed and approved

Community
---------

Getting Help
~~~~~~~~~~~~

- **GitHub Issues**: For bugs and feature requests
- **Discussions**: For questions and general discussion
- **Email**: Contact maintainers directly for sensitive issues

Development Best Practices
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Before Creating New Classes
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Always check existing implementations first:

.. code-block:: bash

   # Check all available classes
   python -m aeroagentsim.helper.class_finder --all

   # Find agents with specific capabilities
   python -m aeroagentsim.helper.class_finder --find-agent position,battery_level

   # Find components with specific metrics
   python -m aeroagentsim.helper.class_finder --find-component speed,processing_power

Component Development
^^^^^^^^^^^^^^^^^^^^^

When creating new components:

- Inherit from the base ``Component`` class
- Implement required abstract methods
- Follow the task execution pattern
- Add appropriate metrics and state updates

Agent Development
^^^^^^^^^^^^^^^^^

When creating new agents:

- Inherit from the base ``Agent`` class
- Define required state variables
- Implement decision-making logic
- Add appropriate event handling

Questions?
----------

If you have questions not covered in this guide:

1. Check existing documentation
2. Search GitHub issues
3. Ask in GitHub Discussions
4. Contact the maintainers

Thank you for contributing to AeroAgentSim! 🚁✨
