# AirFogSim Documentation

Welcome to the AirFogSim documentation hub! This directory contains comprehensive documentation for users, developers, and contributors.

## 📖 For Users

**New to AirFogSim?** Start here:
- [Getting Started Guide](getting_started.html) - Installation and first simulation
- [API Reference](api/index.html) - Complete API documentation
- [User Guide](user_guide.html) - Comprehensive user manual
- [Examples](examples.html) - Ready-to-run examples

**Core Documentation:**
- [Agent Guide](api/agent.html) - Creating and managing agents
- [Component Guide](api/component.html) - Building custom components
- [Workflow Guide](api/workflow.html) - Designing simulation workflows
- [Task Guide](api/task.html) - Implementing custom tasks

## 🔧 For Developers

**Technical Deep Dive:**
- [System Architecture](../src/airfogsim/docs/en/architecture.md) - Detailed system design
- [Development Guide](../src/airfogsim/docs/en/development_guide.md) - Development setup and patterns
- [Component Development](../src/airfogsim/docs/en/component_guide.md) - Creating new components
- [Agent Development](../src/airfogsim/docs/en/agent_guide.md) - Building custom agents

## 🤝 For Contributors

**Contributing to AirFogSim:**
- [Contributing Guide](../CONTRIBUTING.md) - How to contribute code
- [Development Setup](../INSTALL.md) - Setting up development environment
- [Documentation Guide](contributing.html) - Writing documentation

## 🏗️ Building Documentation

### Quick Build
```bash
# Build HTML documentation
python build_docs.py

# Or use CLI tool
airfogsim docs --format html --output-dir ./api_docs
```

### Advanced Build Options
```bash
# Use Sphinx directly
cd docs
make html          # HTML documentation
make latexpdf      # PDF documentation

# Serve locally
python build_docs.py --serve

# Live reload during development
pip install sphinx-autobuild
sphinx-autobuild docs docs/_build/html
```

### Prerequisites
```bash
pip install -e ".[docs]"
# Or: pip install -r docs/requirements.txt
```

## 📁 Documentation Structure

```
docs/
├── README.md              # This navigation file
├── index.rst             # Documentation homepage
├── conf.py               # Sphinx configuration
├── getting_started.rst   # Getting started guide
├── user_guide.rst        # User guide
├── examples.rst          # Examples and tutorials
├── contributing.rst      # Contributing guidelines
├── api/                  # Auto-generated API reference
│   ├── index.rst         # API index
│   ├── core.rst          # Core framework
│   ├── agent.rst         # Agent classes
│   ├── component.rst     # Component classes
│   ├── workflow.rst      # Workflow classes
│   ├── dataprovider.rst  # DataProvider classes
│   ├── manager.rst       # Manager classes
│   ├── task.rst          # Task classes
│   ├── resource.rst      # Resource classes
│   └── ...               # Other API modules
├── _static/              # Static files (CSS, images)
├── _templates/           # Custom templates
└── _build/               # Generated documentation
    └── html/             # HTML output
```

## 🔗 Quick Links

- **Main Project**: [README.md](../README.md) - Project overview
- **Installation**: [INSTALL.md](../INSTALL.md) - Detailed installation guide
- **Examples**: [examples/](../src/airfogsim/examples/) - Code examples
- **Helper Tools**: [helper/](../src/airfogsim/helper/) - Development utilities

## 📝 Documentation Guidelines

When contributing to documentation:

1. **API Documentation**: Auto-generated from docstrings - update source code
2. **User Guides**: Written in reStructuredText (.rst) - update files in this directory
3. **Technical Docs**: Markdown files in `src/airfogsim/docs/` - for deep technical content
4. **Examples**: Include working code examples with explanations

## 🆘 Need Help?

- **Issues**: [GitHub Issues](https://github.com/ZhiweiWei-NAMI/AirFogSim/issues)
- **Discussions**: [GitHub Discussions](https://github.com/ZhiweiWei-NAMI/AirFogSim/discussions)
- **Email**: Contact the development team

---

**Generated Documentation**: After building, open `_build/html/index.html` to browse the complete documentation.

## 🔧 Advanced Documentation Development

### Customization Options

The documentation uses the Read the Docs theme. For advanced customization:

```python
# In conf.py
html_theme_options = {
    'display_version': True,
    'prev_next_buttons_location': 'bottom',
    'style_external_links': False,
}
```

### Troubleshooting

**Common Issues:**
1. **Import errors**: Install AirFogSim in development mode: `pip install -e .`
2. **Missing dependencies**: Install docs dependencies: `pip install -e ".[docs]"`
3. **Build errors**: Check RST syntax and docstring formatting

**Debugging:**
```bash
# Build with warnings as errors
sphinx-build -W -b html docs docs/_build/html

# Clean build cache
python build_docs.py --clean
```

### Writing Guidelines

**Docstring Style** (Google format):
```python
def example_function(param1: str, param2: int = 10) -> bool:
    """Brief description of the function.

    Args:
        param1: Description of the first parameter.
        param2: Description with default value.

    Returns:
        Description of the return value.

    Example:
        >>> result = example_function("hello", 20)
        >>> print(result)
        True
    """
    return True
```

**Cross-references**:
```rst
:class:`airfogsim.core.agent.Agent`
:meth:`airfogsim.core.agent.Agent.execute_task`
:doc:`user_guide`
```

For detailed contributing guidelines, see [Contributing Guide](contributing.html).
