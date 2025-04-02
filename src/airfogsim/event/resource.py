from airfogsim.core.event import Event

class ResourceRequestEvent(Event):
    """Event for requesting resources."""
    
    def __init__(self, source, resource, task_id, amount=0, properties=None, callback=None):
        super().__init__("resource_request", source, resource, {
            "amount": amount,
            "properties": properties or {}
        })
        self.task_id = task_id
        self.callback = callback

class ResourceReleaseEvent(Event):
    """Event for releasing resources."""
    
    def __init__(self, source, resource, allocation_id, callback=None):
        super().__init__("resource_release", source, resource, {
            "allocation_id": allocation_id,
            "resource_id": resource.id
        })
        self.callback = callback