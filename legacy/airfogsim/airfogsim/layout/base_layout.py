from abc import ABCMeta, abstractmethod

class BaseLayout:
    """Base layout class, including the basic methods for layout classes: initialize (resources), render, and close.
    """
    __meta_class__ = ABCMeta

    def __init__(self):
        """The constructor of the BaseLayout class.
        """
        pass
    
    @abstractmethod
    def render(self):
        """Render the layout.
        """
        pass
    
    @abstractmethod
    def close(self):
        """Close the layout.
        """
        pass