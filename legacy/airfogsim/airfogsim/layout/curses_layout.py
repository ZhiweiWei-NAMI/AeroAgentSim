from .base_layout import BaseLayout
class CursesLayout(BaseLayout):
    def __init__(self, config:dict, env):
        """The curses layout for the visualization of the environment.

        Args:
            config (dict): The configuration for the curses layout.
            env (AirFogSimEnv): The AirFogSim environment.
        """
        super().__init__(config, env)
        pass

    def render(self, env):
        """Render the environment.

        Args:
            env (AirFogSimEnv): The environment.
        """
        pass

    def close(self):
        """Close the curses layout.
        """
        pass