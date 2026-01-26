import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from animators.fast_fk_animator import FastFKAnimator
from animators.vae_animator import VaeAnimator
from core.env import ANIMATION_DIR
from core.session_manager import SessionManager, AnimationSession


# Data model for session creation request
class SessionCreateRequest(BaseModel):
    session_id: str
    session_type: str = "FK"  # ex: "FK", "VAE", etc.
    animation_file: str  # ex: "Walking.fbx"

class SpeedRequest(BaseModel):
    playback_speed: float

class FpsRequest(BaseModel):
    fps: float


print("Using animation directory:", ANIMATION_DIR)

# Singleton for session management
manager : SessionManager = SessionManager()

router = APIRouter()

@router.get("/animations")
async def get_all_animations():
    # Retourne la liste de toutes les animations .bvh dans le dossier ./animations
    import os
    try:
        files = os.listdir(ANIMATION_DIR)
        bvh_files = [f for f in files if f.lower().endswith(".bvh")]
        return {"animations": bvh_files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sessions")
async def create_session(req: SessionCreateRequest):
    """Crée une nouvelle session d'animation (lance le process)"""
    try:
        session : AnimationSession = None
        match req.session_type:
            case "FK":
                session  = manager.create_session(
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


@router.get("/sessions/{session_id}/skeleton")
async def get_skeleton(session_id: str):
    """Récupère le squelette statique pour initialiser le client 3D"""
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.skeleton_structure


@router.delete("/sessions/{session_id}")
async def stop_session(session_id: str):
    if not manager.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    await manager.delete_session(session_id)
    return {"status": "deleted"}

# --- NOUVELLES ROUTES PLAY / PAUSE ---

@router.post("/sessions/{session_id}/pause")
async def pause_animation(session_id: str):
    try:
        # manager.pause_session(session_id)
        await manager.dispatch_action(session_id, "pause")
        return {"status": "paused", "session_id": session_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Session introuvable")

@router.post("/sessions/{session_id}/play")
async def play_animation(session_id: str):
    try:
        # manager.resume_session(session_id)
        await manager.dispatch_action(session_id, "play")
        return {"status": "playing", "session_id": session_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Session introuvable")

@router.post("/sessions/{session_id}/speed")
async def set_speed(session_id: str, req: SpeedRequest):
    """
    Règle la vitesse de lecture.
    1.0 = normal, 2.0 = x2, 0.5 = x0.5, -1.0 = Marche arrière
    """
    try:
        # await manager.set_session_speed(session_id, req.playback_speed)
        await manager.dispatch_action(session_id, "set_speed", req.playback_speed)
        return {"status": "updated", "session_id": session_id, "speed": req.playback_speed}
    except ValueError:
        raise HTTPException(status_code=404, detail="Session introuvable")

@router.post("/sessions/{session_id}/fps")
async def set_fps(session_id: str, req: FpsRequest):
    try:
        # await manager.set_session_fps(session_id, req.fps)
        await manager.dispatch_action(session_id, "set_fps", req.fps)
        return {"status": "updated", "session_id": session_id, "fps": req.fps}
    except ValueError:
        raise HTTPException(status_code=404, detail="Session introuvable")
