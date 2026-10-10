# AeroAgentSim Helper Tools

Development utilities and tools for AeroAgentSim framework development and debugging.

## 🔍 Class Finder Tool

The class finder helps developers discover existing classes and avoid duplication.

### 🚀 Quick Usage

**Command Line:**
```bash
# Show all classes
python -m aeroagentsim.helper.class_finder --all

# Show specific class types
python -m aeroagentsim.helper.class_finder --agent
python -m aeroagentsim.helper.class_finder --component
python -m aeroagentsim.helper.class_finder --task
python -m aeroagentsim.helper.class_finder --workflow

# Find classes with specific capabilities
python -m aeroagentsim.helper.class_finder --find-agent position,battery_level
python -m aeroagentsim.helper.class_finder --find-component speed,processing_power
python -m aeroagentsim.helper.class_finder --find-task position,direction
```

**In Code:**
```python
from aeroagentsim.helper import check_all_classes, find_compatible_agents
from aeroagentsim.core.environment import Environment

env = Environment()

# Check all available classes
check_all_classes(env)

# Find agents supporting specific states
find_compatible_agents(env, ['position', 'battery_level'])
```

## 🛠️ Development Workflow

**Before creating new classes**, use the class finder to check existing implementations:

1. **Check existing classes:**
   ```bash
   python -m aeroagentsim.helper.class_finder --all
   ```

2. **Find classes with specific capabilities:**
   ```bash
   # Find agents supporting position and battery states
   python -m aeroagentsim.helper.class_finder --find-agent position,battery_level
   ```

3. **Use existing classes if available**, otherwise create new ones following these conventions:
   - **Agents**: Define `PRODUCED_STATES` attribute
   - **Components**: Define `PRODUCED_METRICS` and `MONITORED_STATES` attributes
   - **Tasks**: Define `NECESSARY_METRICS` and `PRODUCED_STATES` attributes
   - **Workflows**: Define property templates and creation functions

4. **Verify new classes** are properly registered:
   ```bash
   python -m aeroagentsim.helper.class_finder --all
   ```

## 📚 More Information

- **[Development Guide](../docs/en/development_guide.md)** - Detailed development patterns
- **[API Documentation](../../../docs/api/index.html)** - Complete API reference
- **[Main Documentation](../../../docs/README.md)** - Documentation hub
