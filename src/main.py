import logging
import multiprocessing
import os
from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware

from animators.fast_fk_animator import FastFKAnimator
from animators.vae_animator import VaeAnimator
from core.session_manager import SessionManager

ANIMATION_DIR = os.getenv("ANIMATION_DIR")
print("Using animation directory:", ANIMATION_DIR)

logging.basicConfig()
logger = logging.getLogger("FastAPI")
logger.setLevel(logging.DEBUG)

# Singleton for session management
manager = SessionManager()


# Data model for session creation request
class SessionCreateRequest(BaseModel):
    session_id: str
    session_type: str = "FK"  # ex: "FK", "VAE", etc.
    animation_file: str  # ex: "Walking.fbx"

class SpeedRequest(BaseModel):
    playback_speed: float

class FpsRequest(BaseModel):
    fps: float

class VaeValuesRequest(BaseModel):
    vae_values: Annotated[list[float], Query(min_length=3, max_length=3)]

@asynccontextmanager
async def lifespan(app: FastAPI):
    # On Startup Event
    # Initialisation si nécessaire
    # <...>

    yield

    # On Shutdown Event
    for session_id in list(manager.sessions.keys()):
        await manager.delete_session(session_id)


app = FastAPI(title="MoMa Animation Streamer")

# Ceci autorise toutes les origines, toutes les méthodes et tous les headers.
# Pour la prod, remplacez ["*"] par ["http://localhost:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- API REST ---

@app.get("/animations")
async def get_all_animations():
    # Retourne la liste de toutes les animations .bvh dans le dossier ./animations
    import os
    try:
        files = os.listdir(ANIMATION_DIR)
        bvh_files = [f for f in files if f.lower().endswith(".bvh")]
        return {"animations": bvh_files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/sessions")
async def create_session(req: SessionCreateRequest):
    """Crée une nouvelle session d'animation (lance le process)"""
    try:
        # TODO : Implement later a switch case to choose different animator types
        match req.session_type:
            case "FK":
                session = manager.create_session(
                    req.session_id, FastFKAnimator, f"{ANIMATION_DIR}/{req.animation_file}"
                )
            case "VAE":
                session = manager.create_session(
                    req.session_id, VaeAnimator, f"{ANIMATION_DIR}/{req.animation_file}"
                )
            case _:
                raise ValueError(f"Unknown session type: {req.session_type}")

        await session.start()
        return {"status": "created", "session_id": req.session_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/sessions/{session_id}/skeleton")
async def get_skeleton(session_id: str):
    """Récupère le squelette statique pour initialiser le client 3D"""
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.skeleton_structure


@app.delete("/sessions/{session_id}")
async def stop_session(session_id: str):
    if not manager.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    await manager.delete_session(session_id)
    return {"status": "deleted"}

# --- NOUVELLES ROUTES PLAY / PAUSE ---

@app.post("/sessions/{session_id}/pause")
async def pause_animation(session_id: str):
    try:
        manager.pause_session(session_id)
        return {"status": "paused", "session_id": session_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Session introuvable")

@app.post("/sessions/{session_id}/play")
async def play_animation(session_id: str):
    try:
        manager.resume_session(session_id)
        return {"status": "playing", "session_id": session_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Session introuvable")

@app.post("/sessions/{session_id}/speed")
async def set_speed(session_id: str, req: SpeedRequest):
    """
    Règle la vitesse de lecture.
    1.0 = normal, 2.0 = x2, 0.5 = x0.5, -1.0 = Marche arrière
    """
    try:
        await manager.set_session_speed(session_id, req.playback_speed)
        return {"status": "updated", "session_id": session_id, "speed": req.playback_speed}
    except ValueError:
        raise HTTPException(status_code=404, detail="Session introuvable")

@app.post("/sessions/{session_id}/fps")
async def set_fps(session_id: str, req: FpsRequest):
    try:
        await manager.set_session_fps(session_id, req.fps)
        return {"status": "updated", "session_id": session_id, "fps": req.fps}
    except ValueError:
        raise HTTPException(status_code=404, detail="Session introuvable")

@app.post("/sessions/{session_id}/vae_values")
async def set_vae_values(session_id: str, req: VaeValuesRequest):
    try:
        await manager.set_session_vae_values(session_id, req.vae_values)
        return {"status": "updated", "session_id": session_id, "vae_values": req.vae_values}
    except ValueError:
        raise HTTPException(status_code=404, detail="Session introuvable")


# --- WEBSOCKET ---

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    logger.info(f"Nouvelle connexion WS pour la session: {session_id}")
    session = manager.get_session(session_id)
    if not session:
        await websocket.close(code=4000, reason="Session does not exist")
        return

    await session.connect(websocket)
    try:
        # On attend juste que la connexion se ferme
        # Le flux de données est géré par session.broadcast_loop()
        while True:
            # On peut écouter des messages du client si besoin (ex: pause, rewind)
            # data = await websocket.receive_text()
            # Traitement des commandes client ici...

            # Simple keep-alive ou attente passive
            await websocket.receive_text()
    except WebSocketDisconnect:
        session.disconnect(websocket)
    except Exception as e:
        print(f"Erreur WS: {e}")
        session.disconnect(websocket)

# @app.websocket("/ws")
# async def websocket_endpoint(websocket: WebSocket):
#     """
#     Endpoint WebSocket pour le streaming de pose en temps réel.
#     """
#     await websocket.accept()
#
#     # Chaque client connecté obtient sa propre instance de lecteur (Player).
#     # Cela permet à chaque utilisateur d'être à un moment différent de l'anim si besoin,
#     # ou d'avoir ses propres paramètres de lecture.
#     player = AnimationPlayer(anim_data)
#
#     # On lance la lecture
#     player.play()
#     player.loop = True
#
#     # Configuration 30 FPS
#     target_frame_time = 1.0 / 30.0
#
#     # On initialise le "prochain temps cible"
#     next_frame_target = time.perf_counter()
#
#     try:
#         last_time = time.perf_counter()
#         last_print_time = time.perf_counter()
#         message_count = 0
#         total_elapsed = 0.0
#
#         while True:
#             current_time = time.perf_counter()
#             elapsed = current_time - last_time
#             message_count += 1
#             total_elapsed += elapsed
#             last_time = current_time
#
#             # Afficher les stats une fois par seconde
#             if current_time - last_print_time >= 1.0:
#                 mean_time = (
#                     (total_elapsed / message_count) * 1000 if message_count > 0 else 0
#                 )
#                 print(
#                     f"Messages envoyés: {message_count}, Temps moyen: {mean_time:.5f}ms"
#                 )
#                 message_count = 0
#                 total_elapsed = 0.0
#                 last_print_time = current_time
#
#             # 1. Mise à jour du temps interne du player
#             player.update()
#
#             # 2. Récupération des matrices sous forme de bytes (Zero Copy)
#             # Cette méthode a été vue dans animationPlayer.py
#             # pose_bytes = player.get_current_pose()
#             pose_bytes = player.get_current_pose_bytes()
#
#             if pose_bytes is not None:
#                 # await websocket.send_json(json.dumps({
#                 #     'array_data': pose_bytes.tolist()}), mode="text")
#
#                 # header : bytes = struct.pack('<III', 0xBADDF00D, 5, 1)
#                 # payload : bytes = header + pose_bytes
#                 # await websocket.send_bytes(payload)
#
#                 # Fastest
#                 await websocket.send_bytes(pose_bytes)
#
#             # # On calcule quand doit tomber la PROCHAINE frame
#             # next_frame_target += target_frame_time
#             #
#             # now = time.perf_counter()
#             # time_to_wait = next_frame_target - now
#             #
#             # # Si on a beaucoup d'avance (> 2ms), on dort pour économiser le CPU.
#             # # On dort un peu MOINS que prévu (ex: 1.5ms de marge) pour compenser l'imprécision de l'OS.
#             # if time_to_wait > 0.002:
#             #     await asyncio.sleep(time_to_wait - 0.0015)
#             #
#             # # Attente active (Spin-wait) pour la précision finale.
#             # # C'est ce qui garantit le callage parfait sur 33.33ms sans dépasser.
#             # while time.perf_counter() < next_frame_target:
#             #     pass # On ne fait rien, on brûle quelques cycles CPU pour être précis
#
#     except WebSocketDisconnect:
#         print("Client déconnecté")
#         player.stop()
#     except Exception as e:
#         print(f"Erreur inattendue : {e}")
#         await websocket.close()

if __name__ == "__main__":
    # CRITIQUE POUR NUMBA/NUMPY :
    # Force l'utilisation de 'spawn' au lieu de 'fork' (défaut Linux).
    # Cela évite les deadlocks si Numba initialise OpenMP avant le fork.
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        # Déjà défini, on ignore
        pass

    uvicorn.run(app, host="0.0.0.0", port=9810)
