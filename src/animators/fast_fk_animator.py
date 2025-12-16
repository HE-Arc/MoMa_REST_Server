import numpy as np
from typing import Dict, Any

from MoMaFkSolver.core import FastBVH, FastFkSolver

from core.interfaces import AnimatorInterface


class FastFKAnimator(AnimatorInterface):
    def __init__(self):
        self.anim_data: FastFkSolver = None
        self.t = 0.0
        self.num_bones = 50  # Augmentons un peu pour l'exemple
        # 16 floats (matrice 4x4) * 4 octets (float32)
        self.bone_size_bytes = 4 * 4 * np.dtype(np.float64).itemsize
        self.total_size = self.num_bones * self.bone_size_bytes

    def initialize(self, source_path: str):
        self.anim_data = FastBVH(source_path)
        self.num_bones = len(self.anim_data.bone_names)
        self.total_size = self.num_bones * self.bone_size_bytes

    def get_skeleton(self) -> Dict[str, Any]:
        return self.anim_data.get_skeleton_definition()

    def get_memory_size(self) -> int:
        return self.total_size

    def write_frame_to_buffer(self, buffer_view: memoryview, offset: int, dt: float):
        # --- UTILISATION DU DT ---
        # On incrémente le temps interne de l'animation avec la valeur précise reçue du moteur
        self.t += dt

        target_array = np.ndarray(
            shape=(self.num_bones, 4, 4),
            dtype=np.float64,
            buffer=buffer_view,
            offset=offset,
        )

        matrices = self.anim_data.get_pose_at_time_numba(
            self.t, target_array, loop=True, local=False
        )

        if matrices is None:
            return b""

        # # Copy direct des matrices calculées dans la mémoire partagée
        # np.copyto(target_array, matrices)
