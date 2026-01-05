import asyncio
import multiprocessing
import logging
from typing import Dict, Set, Optional, Any
from multiprocessing.shared_memory import SharedMemory
from fastapi import WebSocket

from animators.vae_animator import VaeAnimator
from .engine import AnimationEngine
from .interfaces import AnimatorInterface

logger = logging.getLogger("SessionManager")
logger.setLevel(logging.DEBUG)


class AnimationSession:
    """
    Gère une instance d'animation active :
    - Alloue la mémoire partagée (RAM)
    - Lance le processus moteur (CPU)
    - Diffuse les mises à jour aux clients WebSocket (IO)
    """

    def __init__(
        self, session_id: str, animator_class: type[AnimatorInterface], source_path: str
    ):
        self.session_id = session_id
        self.connections: Set[WebSocket] = set()

        # --- Préparation Infrastructure ---
        # 2. Configuration Mémoire Partagée (Shared Memory)
        # Triple buffering (3 frames d'avance max) pour lisser les pics
        self.buffer_count = 3
        self.queue = multiprocessing.Queue(maxsize=self.buffer_count)
        self.parent_conn, child_conn = multiprocessing.Pipe(duplex=True)

        # VERROU (Lock) : Indispensable pour protéger le Pipe non-thread-safe
        # lors d'accès concurrents depuis FastAPI
        self.pipe_lock = asyncio.Lock()

        self.pause_event = multiprocessing.Event()

        # Variables qui seront remplies après le démarrage du moteur
        self.shm = None
        self.skeleton_structure = None
        self.frame_size = 0

        # 4. Préparation du Moteur (Processus enfant)
        self.animator_class = animator_class
        self.engine = AnimationEngine(
            animator_class,
            source_path,
            self.queue,
            child_conn,
            self.pause_event,
            self.buffer_count,
        )

        self.broadcaster_task = None

    async def execute_command(self, cmd_name: str, args: Any = None, wait_for_response: bool = True, timeout: float = 2.0) -> Any:
        """
        Envoie une commande via Pipe de manière sécurisée (Lock) et asynchrone.
        """
        if not self.engine.is_alive():
            raise RuntimeError("Moteur arrêté.")

        # On verrouille l'accès au Pipe pour cette session.
        # Aucune autre commande ne peut passer tant que celle-ci n'est pas finie (Request/Reply).
        async with self.pipe_lock:
            try:
                # 1. Envoi (Send est non-bloquant pour les petites données)
                # Le tuple contient (cmd, args, expect_response)
                self.parent_conn.send((cmd_name, args, wait_for_response))

                if not wait_for_response:
                    return None

                # 2. Attente Réponse (Recv est BLOQUANT -> run_in_executor)
                loop = asyncio.get_running_loop()

                # On utilise 'poll' avec timeout dans un thread pour éviter de bloquer indéfiniment
                def receive_with_timeout():
                    if self.parent_conn.poll(timeout):
                        return self.parent_conn.recv()
                    raise TimeoutError("Timeout Pipe")

                result, error = await loop.run_in_executor(None, receive_with_timeout)

                if error:
                    raise RuntimeError(f"Erreur Moteur: {error}")
                return result

            except BrokenPipeError:
                raise RuntimeError("Le Moteur a fermé la connexion (crash probable).")
            except Exception as e:
                raise e

    # --- WRAPPERS ---
    async def get_info(self):
        return await self.execute_command("get_info", wait_for_response=True)


    # --- MÉTHODES DE CONTRÔLE ---
    def pause(self):
        """Met l'animation en pause"""
        if not self.pause_event.is_set():
            self.pause_event.set()
            logger.info(f"Session {self.session_id} en pause.")

    def play(self):
        """Reprend l'animation"""
        if self.pause_event.is_set():
            self.pause_event.clear()
            logger.info(f"Session {self.session_id} a repris.")

    async def set_speed(self, speed: float):
        """Change la vitesse de lecture en temps réel"""
        # Modification atomique (process-safe)
        await self.execute_command("set_speed", speed, wait_for_response=False)
        logger.info(f"Session {self.session_id} vitesse réglée à {speed}x")

    async def set_vae_values(self, vae_values: list[float]):
        """Change la vitesse de lecture en temps réel"""
        # Modification atomique (process-safe)
        if self.animator_class is not VaeAnimator:
            return

        await self.execute_command("set_vae_values", vae_values, wait_for_response=False)
        logger.info(f"Session {self.session_id} vae_values réglée à {vae_values}x")

    # ----------------------------

    async def start(self):
        """
        Démarre le moteur, attend son initialisation, configure la mémoire partagée.
        """
        logger.info(f"Session {self.session_id}: Démarrage du moteur...")
        self.engine.start()

        # --- HANDSHAKE D'INITIALISATION ---
        loop = asyncio.get_running_loop()
        try:
            # 1. Attendre que le moteur charge le fichier et renvoie les infos
            # C'est bloquant pour le Pipe, donc on le met dans un thread
            def wait_for_init():
                if self.parent_conn.poll(timeout=60): # Attendre max 10s
                    return self.parent_conn.recv()
                raise TimeoutError("Le moteur n'a pas répondu à l'initialisation.")

            msg_type, data, error = await loop.run_in_executor(None, wait_for_init)

            if msg_type == "init_error":
                raise RuntimeError(f"Le moteur a échoué à charger l'animation : {error}")

            if msg_type != "init_success":
                raise RuntimeError(f"Réponse moteur invalide : {msg_type}")

            # 2. Récupération des données
            self.skeleton_structure = data["skeleton"]
            self.frame_size = data["frame_size"]
            logger.info(f"Session {self.session_id}: Animation chargée. Taille frame: {self.frame_size} bytes")

            # 3. Création de la Shared Memory
            total_mem_size = self.frame_size * self.buffer_count
            self.shm = SharedMemory(create=True, size=total_mem_size)
            logger.info(f"Session {self.session_id}: SHM créée ({self.shm.name})")

            # 4. Envoi du nom SHM au moteur pour qu'il puisse démarrer la boucle
            self.parent_conn.send(("set_shm", self.shm.name, False))

        except Exception as e:
            logger.error(f"Échec démarrage session: {e}")
            self.engine.terminate() # Tuer le processus s'il est bloqué
            raise e

        # --- DÉMARRAGE BROADCAST ---
        self.broadcaster_task = asyncio.create_task(self.broadcast_loop())
        logger.info(f"Session {self.session_id} entièrement opérationnelle.")

    async def stop(self):
        """Arrêt propre et libération des ressources"""
        logger.info(f"Arrêt de la session {self.session_id}...")

        # Arrêt de la boucle de broadcast
        if self.broadcaster_task:
            self.broadcaster_task.cancel()
            try:
                await self.broadcaster_task
            except asyncio.CancelledError:
                pass

        # On prévient le moteur de s'arrêter proprement
        try:
            self.parent_conn.send(("stop", None, False))
        except:
            pass

        # Arrêt du processus moteur
        self.engine.stop()
        self.engine.join(timeout=2)

        # Si ça bloque toujours -> Terminate
        if self.engine.is_alive():
            self.engine.terminate()

        # Fermeture des WebSockets
        for connection in list(self.connections):
            await connection.close()
        self.connections.clear()

        # NETTOYAGE CRITIQUE DE LA MÉMOIRE PARTAGÉE
        # Si on oublie ça, la RAM du serveur se remplit indéfiniment (memory leak)
        try:
            self.shm.close()
            self.shm.unlink()  # Demande à l'OS de détruire le fichier mémoire
            logger.info(f"Mémoire partagée {self.shm.name} libérée.")
        except FileNotFoundError:
            pass  # Déjà nettoyé

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.connections:
            self.connections.remove(websocket)

    async def broadcast_loop(self):
        """
        Boucle IO haute performance :
        Lit l'index depuis la Queue -> Lit la RAM partagée -> Envoie les bytes
        """
        logger.info("Boucle de broadcast démarrée.")

        loop = asyncio.get_running_loop()

        while True:
            try:
                # 1. Attente non-bloquante de la prochaine frame disponible
                slot_index = await loop.run_in_executor(None, self.queue.get)

                if not self.connections:
                    continue

                # 2. Zero-Copy Slice
                # On crée une vue sur la zone mémoire spécifique à cette frame
                offset = slot_index * self.frame_size
                frame_view = self.shm.buf[offset : offset + self.frame_size]

                # 3. Broadcast parallèle
                # starlette/uvicorn gère l'envoi de memoryview efficacement
                await asyncio.gather(
                    *[client.send_bytes(frame_view) for client in self.connections],
                    return_exceptions=True,
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur broadcast session {self.session_id}: {e}")


# ---------------------------------------------------------------------------
# C'EST ICI QUE JE L'AVAIS OUBLIÉ : La classe SessionManager
# ---------------------------------------------------------------------------


class SessionManager:
    """
    Singleton (ou instance globale) qui garde une référence vers toutes les sessions actives.
    Permet de créer, récupérer et supprimer des sessions depuis l'API REST.
    """

    def __init__(self):
        self.sessions: Dict[str, AnimationSession] = {}

    def create_session(
        self, session_id: str, animator_cls: type[AnimatorInterface], path: str
    ) -> AnimationSession:
        """Crée une nouvelle session (mais ne la démarre pas forcément tout de suite)"""
        if session_id in self.sessions:
            raise ValueError(f"La session {session_id} existe déjà.")

        session = AnimationSession(session_id, animator_cls, path)
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[AnimationSession]:
        return self.sessions.get(session_id)

    async def delete_session(self, session_id: str):
        """Arrête proprement une session et la retire de la liste"""
        if session_id in self.sessions:
            await self.sessions[session_id].stop()
            del self.sessions[session_id]
            logger.info(f"Session {session_id} supprimée du manager.")

    # --- WRAPPERS POUR LE CONTRÔLE ---
    def pause_session(self, session_id: str):
        session = self.get_session(session_id)
        if session:
            session.pause()
        else:
            raise ValueError("Session introuvable")

    def resume_session(self, session_id: str):
        session = self.get_session(session_id)
        if session:
            session.play()
        else:
            raise ValueError("Session introuvable")

    async def set_session_speed(self, session_id: str, speed: float):
        s = self.get_session(session_id)
        if s:
            return await s.set_speed(speed)
        else:
            raise ValueError("Session introuvable")

    async def set_session_vae_values(self, session_id: str, vae_values: list[float]):
        s = self.get_session(session_id)
        if s:
            return await s.set_vae_values(vae_values)
        else:
            raise ValueError("Session introuvable")
