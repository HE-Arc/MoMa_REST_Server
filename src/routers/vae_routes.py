from typing import Annotated

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from core.session_manager import SessionManager


class VaeValuesRequest(BaseModel):
    vae_values: Annotated[list[float], Query(min_length=3, max_length=3)]


# Singleton for session management
manager: SessionManager = SessionManager()

router = APIRouter()


@router.post("/sessions/{session_id}/vae_values")
async def set_vae_values(session_id: str, req: VaeValuesRequest):
    try:
        # await manager.set_session_vae_values(session_id, req.vae_values)
        await manager.dispatch_action(
            session_id,
            "set_vae_values",
            np.array([float(p) for p in list(req.vae_values)]),
        )
        return {
            "status": "updated",
            "session_id": session_id,
            "vae_values": req.vae_values,
        }
    except ValueError:
        raise HTTPException(status_code=404, detail="Session introuvable")
