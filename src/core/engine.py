import multiprocessing
import time
import logging
import traceback
from multiprocessing.shared_memory import SharedMemory

from .interfaces import AnimatorInterface

logging.basicConfig()
logger = logging.getLogger("AnimationEngine")
logger.setLevel(logging.INFO)


class AnimationEngine(multiprocessing.Process):
    def __init__(
        self,
        animator_class: type[AnimatorInterface],
        source_path: str,
        frame_queue: multiprocessing.Queue,
        shm_name: str,
        frame_size: int,
        pause_event: multiprocessing.Event,
        buffer_count: int = 3,
        fps: int = 30,
    ):
        super().__init__()
        self.animator_class = animator_class
        self.source_path = source_path
        self.frame_queue = frame_queue
        self.shm_name = shm_name
        self.frame_size = frame_size
        self.buffer_count = buffer_count
        self.fps = fps
        self.running = multiprocessing.Event()
        self.target_frame_time = 1.0 / self.fps

        self.pause_event = pause_event


    def run(self):
        logger.info(f"Moteur démarré (Shared Memory: {self.shm_name})")
        shm = None
        try:
            # 1. Attacher à la mémoire partagée existante
            shm = SharedMemory(name=self.shm_name)

            # 2. Initialiser l'animateur
            animator = self.animator_class()
            animator.initialize(self.source_path)

            self.running.set()
            buffer_index = 0

            while self.running.is_set():
                if self.pause_event.is_set():
                    time.sleep(0.1)
                    continue

                start_time = time.perf_counter()

                # Calcul de l'offset dans le grand bloc mémoire
                offset = buffer_index * self.frame_size

                # 3. Écriture DIRECTE (Zero-Copy)
                # L'animateur écrit ses floats directement dans la RAM partagée
                animator.write_frame_to_buffer(shm.buf, offset, self.target_frame_time)

                # 4. Notification
                # On envoie juste l'index (un simple int), c'est instantané.
                if not self.frame_queue.full():
                    self.frame_queue.put(buffer_index)
                    # Avancer l'index (0 -> 1 -> 2 -> 0 ...)
                    buffer_index = (buffer_index + 1) % self.buffer_count

                # 5. Timing
                elapsed = time.perf_counter() - start_time
                sleep_time = self.target_frame_time - elapsed
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
