import multiprocessing
import time
import logging
import traceback
from multiprocessing.shared_memory import SharedMemory

import numpy as np

from animators.vae_animator import VaeAnimator
from .interfaces import AnimatorInterface

logging.basicConfig()
logger = logging.getLogger("AnimationEngine")
logger.setLevel(logging.INFO)

# Commandes gérées par le moteur lui-même (infrastructure)
SYSTEM_COMMANDS = {"seek", "set_fps", "get_info", "set_speed"}

# noinspection D
class AnimationEngine(multiprocessing.Process):
    def __init__(
        self,
        animator_class,
        source_path: str,
        frame_queue: multiprocessing.Queue,
        command_conn: multiprocessing.connection.Connection,
        pause_event: multiprocessing.Event,
        buffer_count: int = 3,
        fps: int = 60,
    ):
        super().__init__()
        self.animator = None
        self.animator_class = animator_class
        self.source_path = source_path
        self.frame_queue = frame_queue
        self.command_conn = command_conn

        # Note : On ne connaît pas encore le nom de la SHM ni la taille frame
        self.shm_name = None
        self.frame_size = 0

        self.buffer_count = buffer_count
        self.engine_fps = fps
        self.engine_target_frame_time = 1.0 / self.engine_fps
        self.running = multiprocessing.Event()
        self.playback_speed_value = 1.0

        self.pause_event = pause_event

    def _wait_for_shm_config(self):
        """
        Bloque jusqu'à recevoir le nom de la mémoire partagée depuis le processus parent.
        """
        logging.info("Moteur: Attente de la configuration SHM...")
        while True:
            if self.command_conn.poll(timeout=10):  # Timeout de sécurité
                msg = self.command_conn.recv()
                cmd_name, args, _ = msg

                if cmd_name == "set_shm":
                    self.shm_name = args
                    return True
                elif cmd_name == "stop":
                    return False
            else:
                logging.warning("Moteur: Timeout en attente de SHM")
                return False

    def _process_commands(self, animator):
        """
        Vérifie le Pipe. Si des données sont là, on les lit.

        Traitement dynamique des commandes :
        1. Vérifie si c'est une commande système (set_fps...)
        2. Sinon, cherche la méthode sur l'animateur via introspection
        """
        # .poll() retourne True immédiatement s'il y a des données, False sinon.
        # C'est non-bloquant et très rapide.
        while self.command_conn.poll():
            try:
                # Lecture bloquante mais instantanée car poll() a dit ok
                message = self.command_conn.recv()

                # Format message: (cmd_name, args, expect_response)
                # expect_response est un booléen pour savoir si on doit renvoyer quelque chose
                cmd_name, args, expect_response = message

                result = None
                error = None

                try:
                    # 1. Commandes Système (Prioritaires)
                    if cmd_name in SYSTEM_COMMANDS:
                        if cmd_name == "set_fps":
                            self.engine_fps = float(args)
                            self.engine_target_frame_time = 1.0 / self.engine_fps
                            result = self.engine_fps

                        elif cmd_name == "seek":
                            if hasattr(animator, "seek"):
                                animator.seek(args)
                            logging.info(f"Moteur: Seek vers {args}s")
                            result = "ok"

                        elif cmd_name == "set_speed":
                            self.playback_speed_value = float(args)
                            # logging.info(f"Moteur: Vitesse changée à {self.current_speed}x")
                            result = self.playback_speed_value

                        elif cmd_name == "get_info":
                            result = {
                                "source": self.source_path,
                                "fps": self.fps,
                                "shm": self.shm_name,
                                "frame_size": self.frame_size
                            }
                            # On ajoute l'info de l'animateur s'il a une propriété current_time
                            if hasattr(animator, "current_time"):
                                result["time"] = animator.current_time

                    # 2. Commandes Animateur (Dynamique)
                    elif hasattr(animator, cmd_name):
                        method = getattr(animator, cmd_name)

                        # VÉRIFICATION DE SÉCURITÉ (@expose)
                        if getattr(method, "_is_exposed", False):

                            # Invocation dynamique
                            if isinstance(args, dict):
                                result = method(**args) # Arguments nommés
                            elif isinstance(args, list) or isinstance(args, tuple):
                                result = method(*args) # Arguments positionnels
                            elif args is None:
                                result = method() # Sans argument
                            else:
                                result = method(args) # Argument unique
                        else:
                            error = f"Method '{cmd_name}' exists but is not exposed via @expose"

                    # elif cmd_name == "set_vae_values":
                    #     if self.animator_class is not VaeAnimator:
                    #         raise ValueError("Animator is not VaeAnimator, cannot set VAE values.")
                    #
                    #     floats = np.array([float(p) for p in list(args)])
                    #     self.animator.anim_data.set_vae_values(floats)
                    #     # logging.info(f"Moteur: Vitesse changée à {self.current_speed}x")
                    #     result = self.animator.anim_data.vae_values

                    else:
                        error = f"Unknown command: {cmd_name}"

                except Exception as ex:
                    logging.error(f"Erreur commande {cmd_name}: {ex}")
                    error = str(ex)

                # --- REPONSE ---
                # Avec un Pipe Duplex, on renvoie la réponse directement sur la même connexion
                if expect_response:
                    self.command_conn.send((result, error))

            except Exception as e:
                logging.error(f"Erreur critique traitement Pipe: {e}")
                break


    def run(self):
        try:
            import importlib
            import keras

            # 1. Import du module pour charger la définition de classe
            importlib.import_module("skanym.structures.network.vae")
            from skanym.structures.network.vae import VAE

            # 2. Enregistrement sous le nom court 'VAE'
            keras.saving.register_keras_serializable(name="VAE")(VAE)

            # 3. Fallback : Injection directe dans le registre global (Ceinture et bretelles)
            if hasattr(keras.saving, "get_custom_objects"):
                keras.saving.get_custom_objects()["VAE"] = VAE

            logger.info("Moteur: Classe VAE enregistrée manuellement (name='VAE').")

            # --- PHASE 1 : CHARGEMENT LOURD ---
            logger.info(f"Moteur: Chargement de {self.source_path}...")

            self.animator = self.animator_class()
            self.animator.initialize(self.source_path)

            # Récupération des métadonnées
            skeleton = self.animator.get_skeleton()
            self.frame_size = self.animator.get_memory_size()

            # Envoi du succès au parent via le Pipe
            # On envoie : (status, data, error)
            logger.info("Moteur: Chargement terminé. Envoi des métadonnées.")
            self.command_conn.send(("init_success", {
                "skeleton": skeleton,
                "frame_size": self.frame_size
            }, None))

        except Exception as e:
            logger.error(f"Erreur d'initialisation Moteur: {e}")
            self.command_conn.send(("init_error", None, str(e)))
            return # Arrêt immédiat


        # --- PHASE 2 : ATTENTE SHM ---
        if not self._wait_for_shm_config():
            logger.error("Moteur: Échec configuration SHM. Arrêt.")
            return

        # --- PHASE 3 : BOUCLE PRINCIPALE ---
        shm = None
        try:
            logger.info(f"Moteur: Attachement à SHM {self.shm_name}")
            shm = SharedMemory(name=self.shm_name)
            self.running.set()
            buffer_index = 0

            while self.running.is_set():
                start_time = time.perf_counter()

                # 1. Commandes via Pipe
                self._process_commands(self.animator)

                if self.pause_event.is_set():
                    time.sleep(0.1)
                    continue

                # Calcul de l'offset dans le grand bloc mémoire
                offset = buffer_index * self.frame_size

                # 3. Écriture DIRECTE (Zero-Copy)
                # L'animateur écrit ses floats directement dans la RAM partagée
                self.animator.write_frame_to_buffer(
                    shm.buf,
                    dt=self.engine_target_frame_time,
                    offset=offset,
                    playback_speed=self.playback_speed_value,
                )

                # 4. Notification
                # On envoie juste l'index (un simple int), c'est instantané.
                if not self.frame_queue.full():
                    self.frame_queue.put(buffer_index)
                    # Avancer l'index (0 -> 1 -> 2 -> 0 ...)
                    buffer_index = (buffer_index + 1) % self.buffer_count

                # 5. Timing
                elapsed = time.perf_counter() - start_time
                sleep_time = self.engine_target_frame_time - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except Exception as e:
            logger.error(f"Erreur Moteur: {e}")
            logger.error(traceback.format_exc())
        finally:
            if shm:
                shm.close()  # Détacher, mais ne pas unlink (le manager le fera)
            logger.info("Arrêt moteur.")

    def stop(self):
        self.running.clear()
