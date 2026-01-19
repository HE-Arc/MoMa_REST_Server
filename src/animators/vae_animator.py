import logging
import os
from typing import Dict, Any
from pathlib import Path

import numpy as np
from skanym.structures.network.vae import VAE

from core.interfaces import AnimatorInterface
import skanym as sk
from skanym.utils.character import remove_fingers
from skanym.animators.vaeAnimator import VaeAnimator as skVaeAnimator
from skanym.loaders import assimpLoader

from core.utils import list_files

VAE_DIR = os.getenv("VAE_DIR")
print("Using vae directory:", VAE_DIR)

logging.basicConfig()
logger = logging.getLogger("VaeAnimator")
logger.setLevel(logging.INFO)


class VaeAnimator(AnimatorInterface):
    def __init__(self):
        self.anim_data: skVaeAnimator = None
        self.t = 0.0
        self.num_bones = 50
        # 16 floats (matrice 4x4) * 4 octets (float32)
        self.bone_size_bytes = 4 * 4 * np.dtype(np.float64).itemsize
        self.total_size = self.num_bones * self.bone_size_bytes

    @property
    def animator_fps(self):
        pass

    @property
    def animator_frametime(self):
        pass

    def initialize(self, source_path: str):
        loader = sk.loaders.assimpLoader.AssimpLoader(Path(""))
        skeletons = []
        animations = []
        VAE_ANIMATION_DICT = list_files(VAE_DIR, [".fbx"])
        print(VAE_ANIMATION_DICT)
        # for anim_name in VAE_ANIMATION_DICT:
        #     logger.info("Loading animation {}".format(anim_name))
        #     loader.set_path(Path(VAE_ANIMATION_DICT.get(anim_name)))
        #     current_skeleton = loader.load_skeleton()
        #     current_anim = loader.load_animation()
        #     current_skeleton, current_anim = sk.remove_fingers(
        #         current_skeleton, current_anim
        #     )
        #     skeletons.append(current_skeleton)
        #     animations.append(current_anim)

        anim_name = "run_kh75_sp50_as30.fbx"
        logger.info("Loading animation {}".format(anim_name))
        loader.set_path(Path(VAE_ANIMATION_DICT.get(anim_name)))
        current_skeleton = loader.load_skeleton()
        current_anim = loader.load_animation()
        current_skeleton, current_anim = remove_fingers(current_skeleton, current_anim)

        self.skeleton = (
            current_skeleton  # Skeleton is loaded from the last animation in the dict
        )

        self.anim_data = skVaeAnimator(
            (self.skeleton),
            animations,
            3,
            int(30.0),
            model_path= VAE_DIR + "/model/cvae_b10.0_l3",
        )  # MAGIC NUMBERS

        # PATCH: Injection de l'attribut manquant 'rotation' pour les anciens modèles
        if hasattr(self.anim_data, "model") and not hasattr(
            self.anim_data.model, "rotation"
        ):
            logger.warning(
                "Modèle CVAE chargé sans attribut 'rotation'. Application de la valeur par défaut 'quaternion'."
            )
            self.anim_data.model.rotation = "quaternion"

        self.num_bones = self.skeleton.get_nb_joints()
        self.total_size = self.num_bones * self.bone_size_bytes

    def get_skeleton(self) -> Dict[str, Any]:
        self.bone_names = [name for name in self.skeleton.as_joint_dict().values()]
        # Forced to convert to int type, otherwise the json serializer fails
        parents_list = [int(x) for x in self.skeleton.as_parent_id_vector()]
        parents_list[0] = -1

        num_bones = self.skeleton.get_nb_joints()

        bind_pose_dict = self.skeleton.as_bind_pose_dict()
        # Forced to convert to float type, otherwise the json serializer fails
        r_pos = [[float(x) for x in val["pos"]] for val in bind_pose_dict.values()]
        r_rot = [[float(x) for x in val["orient"]] for val in bind_pose_dict.values()]
        r_scl = [[1, 1, 1]] * num_bones

        return {
            "type": "SKELETON_DEF",
            "bone_names": self.bone_names,
            "parents": parents_list,
            "bind_pose": {"positions": r_pos, "rotations": r_rot, "scales": r_scl},
        }

    def get_memory_size(self) -> int:
        return self.total_size

    def write_frame_to_buffer(
        self, buffer_view: memoryview, offset: int, dt: float, playback_speed: float
    ):
        # Index 2 contains the global transformation matrices
        output_lst = self.anim_data.step(dt * playback_speed)
        global_mat = output_lst[2]

        target_array = np.ndarray(
            shape=(self.num_bones, 4, 4),
            dtype=np.float64,
            buffer=buffer_view,
            offset=offset,
        )

        np.copyto(target_array, global_mat)
