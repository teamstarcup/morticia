import inspect
from typing import Callable, Any, Coroutine


class BaseEvent:
    pass


class MessageEvent(BaseEvent):
    title: str
    message: str

    def __init__(self, title: str, message: str):
        super().__init__()
        self.title = title
        self.message = message


class Publisher:
    def __init__(self):
        self._subscriptions: set[tuple[type, Callable[[BaseEvent],Coroutine[Any,Any,None]]]] = set()
        self._subscribers: dict[Any, list[Any]] = {}

    def subscribe(self, subscriber: Any):
        subscriptions = self._subscribers.get(subscriber, [])
        method_names = [method_name for method_name in dir(subscriber) if callable(getattr(subscriber, method_name))]
        for method_name in method_names:
            if not method_name.startswith("receive_"):
                continue
            method = getattr(subscriber, method_name)
            event_type = inspect.signature(method).parameters["event"].annotation
            subscription = (event_type, method)
            self._subscriptions.add(subscription)
            subscriptions.append(subscription)
        self._subscribers[subscriber] = subscriptions

    def unsubscribe(self, subscriber: Any):
        subscriptions = self._subscribers[subscriber]
        for subscription in subscriptions:
            self._subscriptions.remove(subscription)

    async def publish(self, event: BaseEvent):
        info = [meta for meta in self._subscriptions if meta[0] == type(event)]
        for entry in info:
            _, subscription = entry
            await subscription(event)
