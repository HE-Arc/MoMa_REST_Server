from abc import ABC, abstractmethod
from typing import Any, Callable, Dict


def expose(func: Callable):
    """
    Décorateur à placer sur les méthodes de l'animateur.
    Marque la méthode comme étant appelable publiquement via l'API REST.
    """
    func._is_exposed = True
    return func


class AnimatorInterface(ABC):
    @property
    @abstractmethod
    def animator_fps(self):
        pass

    @property
    @abstractmethod
    def animator_frametime(self):
        pass

    @abstractmethod
    def initialize(self, source_path: str):
        pass

    @abstractmethod
    def get_skeleton(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_memory_size(self) -> int:
        """
        Retourne la taille exacte en octets d'une frame.
        Nécessaire pour allouer la mémoire partagée.
        Ex: 10 os * 16 floats * 4 bytes = 640 bytes.
        """
        pass

    @abstractmethod
    def write_frame_to_buffer(
        self, buffer_view: memoryview, offset: int, dt: float, playback_speed: float
    ):
        """
        Écrit les données de la frame directement dans le buffer mémoire fourni.
        'buffer_view' est une vue sur la mémoire partagée globale.
        'offset' est l'endroit où commencer à écrire.
        """
        pass
