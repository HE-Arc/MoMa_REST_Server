import asyncio
import multiprocessing
import logging
from typing import Dict, Set, Optional
from multiprocessing.shared_memory import SharedMemory
from fastapi import WebSocket
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

        # 1. Initialisation temporaire pour connaître la taille mémoire requise
        # On instancie l'animateur juste pour lire ses métadonnées, puis on le jette.
        temp_animator = animator_class()
        temp_animator.initialize(source_path)
        self.skeleton_structure = temp_animator.get_skeleton()
        self.frame_size = temp_animator.get_memory_size()

        # 2. Configuration Mémoire Partagée (Shared Memory)
        # Triple buffering (3 frames d'avance max) pour lisser les pics
        self.buffer_count = 3
        total_mem_size = self.frame_size * self.buffer_count

        # Création du bloc mémoire physique
        self.shm = SharedMemory(create=True, size=total_mem_size)
        logger.info(
            f"Session {session_id}: RAM allouée {total_mem_size} bytes (SHM: {self.shm.name})"
        )

        # 3. Queue légère de synchronisation
        # Ne transporte que des entiers (index 0, 1 ou 2), pas de données lourdes.
        self.queue = multiprocessing.Queue(maxsize=self.buffer_count)

        self.pause_event = multiprocessing.Event()

        # 4. Préparation du Moteur (Processus enfant)
        self.engine = AnimationEngine(
            animator_class,
            source_path,
            self.queue,
            self.shm.name,  # On passe le nom pour qu'il puisse s'y attacher
            self.frame_size,
            self.pause_event,
            self.buffer_count,
        )
        self.broadcaster_task = None

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

    # ----------------------------

    async def start(self):
        """Démarre le calcul et la diffusion"""
        self.engine.start()

        self.broadcaster_task = asyncio.create_task(self.broadcast_loop())
        logger.info(f"Session {self.session_id} démarrée.")

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

        # Arrêt du processus moteur
        self.engine.stop()
        self.engine.join(timeout=2)

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
