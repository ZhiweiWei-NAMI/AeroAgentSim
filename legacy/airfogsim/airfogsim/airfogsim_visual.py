from .layout.tkinter_layout import TkinterLayout
from .layout.curses_layout import CursesLayout

class AirFogSimEnvVisualizer():
    def __init__(self, mode:str='graphic', config:dict=None, env=None):
        """The visualizer for AirFogSimEnv. It provides the visualization of the environment. Agent interacts with this class to get the visualized state, reward, and done signal.
        
        Args:
            mode (str, optional): The mode of the visualizer. 'graphic' or 'text'. Defaults to 'graphic'.
        """
        self._mode = mode
        self._env = env
        assert self._mode in ['graphic', 'text'], f"Invalid mode: {self._mode}"
        self._initalize_layout(config)

    def _initalize_layout(self, config):
        """Initialize the layout of the visualizer. If the mode is 'graphic', it initializes the tkinter window. If the mode is 'text', it initializes the text layout.
        """
        if self._mode == 'graphic':
            self._layout = TkinterLayout(config, self._env)
        elif self._mode == 'text':
            self._layout = CursesLayout(config, self._env)  

    def render(self, env):
        """Render the environment.

        Args:
            env (AirFogSimEnv): The environment.
        """
        self._layout.render()