from abc import ABC, abstractmethod


class NotificationProvider(ABC):
    @abstractmethod
    def send(self, message: str) -> None:
        ...
