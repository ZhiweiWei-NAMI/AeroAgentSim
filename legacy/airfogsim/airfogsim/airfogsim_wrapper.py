class AirFogSimEnvWrapper():
    """Visual wrapper for the environment. This wrapper will render the environment and provide the visual feedback to the agent.
    """
    def __init__(self, env, config):
        self.env = env
        # according to config decide whether to use graphics or terminal only