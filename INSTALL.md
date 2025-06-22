# AirFogSim Installation Guide

This document provides detailed installation instructions for AirFogSim, including all dependencies and setup requirements.

## System Requirements

### Operating System
- Linux (Ubuntu 18.04+, CentOS 7+, or similar)
- macOS 10.14+
- Windows 10+ (with WSL2 recommended for best compatibility)

### Python Requirements
- **Python 3.8 or higher** (Python 3.10+ recommended)
- pip (Python package installer)
- virtualenv or conda (recommended for environment management)

### Hardware Requirements
- **Minimum**: 4GB RAM, 2GB free disk space
- **Recommended**: 8GB+ RAM, 5GB+ free disk space
- **For visualization**: Additional 2GB RAM, graphics card with OpenGL support

## Installation Methods

### Method 1: Install from PyPI (Recommended for Users)

This is the simplest method for users who want to use AirFogSim without modifying the source code.

```bash
# Create and activate a virtual environment (recommended)
python -m venv airfogsim_env
source airfogsim_env/bin/activate  # On Windows: airfogsim_env\Scripts\activate

# Install AirFogSim
pip install airfogsim

# Verify installation
python -c "import airfogsim; print('AirFogSim installed successfully!')"
```

### Method 2: Install from Source (Recommended for Developers)

This method is recommended for developers who want to contribute to AirFogSim or need the latest features.

#### Step 1: Clone the Repository

```bash
git clone https://github.com/ZhiweiWei-NAMI/AirFogSim.git
cd AirFogSim
```

#### Step 2: Create Virtual Environment

```bash
# Using venv (built-in)
python -m venv airfogsim_env
source airfogsim_env/bin/activate  # On Windows: airfogsim_env\Scripts\activate

# OR using conda
conda create -n airfogsim python=3.10
conda activate airfogsim
```

#### Step 3: Install Dependencies

```bash
# Install in development mode with all dependencies
pip install -e .[dev]

# OR install basic dependencies only
pip install -e .
```

#### Step 4: Verify Installation

```bash
# Test core functionality
python -c "import airfogsim; print('AirFogSim installed successfully!')"

# Run example tests
cd src/airfogsim/examples
python test_examples.py --list

# Test CLI functionality
airfogsim classes --type all
```

## Optional Dependencies

### For Visualization System

If you want to use the web-based visualization interface:

```bash
# Install Node.js and npm (if not already installed)
# Ubuntu/Debian:
sudo apt update
sudo apt install nodejs npm

# macOS (using Homebrew):
brew install node npm

# Windows: Download from https://nodejs.org/

# Install frontend dependencies
cd frontend
npm install
cd ..
```

### For Weather Data Integration

If you plan to use real weather data:

```bash
# No additional packages needed - uses requests (already included)
# You'll need to obtain API keys from weather service providers
```

### For Advanced Development

```bash
# Install additional development tools
pip install black isort mypy pre-commit

# Set up pre-commit hooks (optional)
pre-commit install
```

## Configuration

### Environment Variables

Create a `.env` file in your project directory for configuration:

```bash
# Optional: OpenWeatherMap API key for weather data
OPENWEATHERMAP_API_KEY=your_api_key_here

# Optional: Other API keys for external services
# CUSTOM_API_KEY=your_custom_key
```

### Logging Configuration

AirFogSim uses Python's built-in logging. You can configure logging levels:

```python
import logging
logging.basicConfig(level=logging.INFO)  # or DEBUG, WARNING, ERROR
```

## Verification and Testing

### Quick Verification

```bash
# Test basic imports
python -c "
from airfogsim.core.environment import Environment
from airfogsim.agent.drone import DroneAgent
print('Core classes imported successfully!')
"

# Test CLI
airfogsim --help
```

### Run Test Suite

```bash
# Install test dependencies (if not already installed)
pip install pytest pytest-cov

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=airfogsim --cov-report=html
```

### Run Examples

```bash
# Navigate to examples directory
cd src/airfogsim/examples

# List all available examples
python test_examples.py --list

# Run specific examples
python test_examples.py --run example_workflow_diagram example_trigger_basic

# Run all examples (may take several minutes)
python test_examples.py
```

## Troubleshooting

### Common Issues

#### Import Errors
```bash
# If you get import errors, ensure the package is properly installed
pip install -e .

# Check if the package is in Python path
python -c "import sys; print(sys.path)"
```

#### Permission Errors
```bash
# On Linux/macOS, you might need to use --user flag
pip install --user airfogsim

# Or use sudo (not recommended)
sudo pip install airfogsim
```

#### Virtual Environment Issues
```bash
# Ensure virtual environment is activated
which python  # Should point to your virtual environment

# Recreate virtual environment if needed
deactivate
rm -rf airfogsim_env
python -m venv airfogsim_env
source airfogsim_env/bin/activate
```

#### Node.js/npm Issues (for visualization)
```bash
# Clear npm cache
npm cache clean --force

# Remove node_modules and reinstall
cd frontend
rm -rf node_modules package-lock.json
npm install
```

### Getting Help

If you encounter issues not covered here:

1. Check the [GitHub Issues](https://github.com/ZhiweiWei-NAMI/AirFogSim/issues)
2. Review the [documentation](docs/README.md)
3. Run the diagnostic script: `python test_package.py`
4. Create a new issue with:
   - Your operating system and Python version
   - Complete error messages
   - Steps to reproduce the problem

## Performance Optimization

### For Large Simulations

```bash
# Install performance-optimized packages
pip install numpy scipy pandas

# Consider using PyPy for CPU-intensive simulations
# (Note: Some dependencies may not be compatible)
```

### Memory Management

```bash
# Monitor memory usage during simulations
pip install psutil memory_profiler

# Use in your code:
# from memory_profiler import profile
# @profile
# def your_simulation_function():
#     pass
```

## Next Steps

After successful installation:

1. **[Project Overview](README.md)** - Read the main README for an overview
2. **[Examples](src/airfogsim/examples/)** - Check out ready-to-run examples
3. **[Documentation Hub](docs/README.md)** - Browse complete documentation
4. **[User Guide](docs/user_guide.html)** - Comprehensive usage guide
5. **[Contributing](CONTRIBUTING.md)** - Join our development community

---

**Note**: This installation guide is regularly updated. If you find any issues or have suggestions for improvement, please let us know!
