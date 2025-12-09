import asyncio
import time
from pathlib import Path

from MoMaFkSolver.core import FastBVH
from MoMaFkSolver.player import AnimationPlayer
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.middleware.cors import CORSMiddleware

# Importations basées sur votre structure de fichiers

app = FastAPI()

# Ceci autorise toutes les origines, toutes les méthodes et tous les headers.
# Pour la prod, remplacez ["*"] par ["http://localhost:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. Chargement de l'animation au démarrage du serveur
# On utilise la logique vue dans tests/test.py pour localiser le fichier
BVH_PATH = Path("animations/07_01.bvh")

print(f"Chargement de l'animation depuis : {BVH_PATH}")
# On charge l'animation en mémoire une seule fois (Singleton pattern implicite)
anim_data = FastBVH(str(BVH_PATH))


@app.get("/")
async def root():
    return {"message": "MM Server Reforged est en ligne. Utilisez /ws pour le streaming."}


@app.get("/skeleton")
async def get_skeleton_definition():
    """
    Endpoint REST pour récupérer la structure statique du squelette.
    Le client doit appeler ceci EN PREMIER pour construire ses objets 3D.
    """
    # La méthode get_skeleton_definition a été définie dans skeletalAnimation.py
    # Elle retourne les noms des os, les parents et la Bind Pose.
    return anim_data.get_skeleton_definition()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Endpoint WebSocket pour le streaming de pose en temps réel.
    """
    await websocket.accept()

    # Chaque client connecté obtient sa propre instance de lecteur (Player).
    # Cela permet à chaque utilisateur d'être à un moment différent de l'anim si besoin,
    # ou d'avoir ses propres paramètres de lecture.
    player = AnimationPlayer(anim_data) #

    # On lance la lecture
    player.play()
    player.loop = True

    # Fréquence d'envoi cible (ex: 30 FPS => 0.033s)
    target_frame_time = 0.033

    try:
        last_print_time = time.time()
        message_count = 0
        total_elapsed = 0.0

        while True:
            start_process = time.time()

            # 1. Mise à jour du temps interne du player
            player.update()

            # 2. Récupération des matrices sous forme de bytes (Zero Copy)
            # Cette méthode a été vue dans animationPlayer.py
            pose_bytes = player.get_current_pose_bytes()

            if pose_bytes:
                elapsed = time.time() - start_process
                message_count += 1
                total_elapsed += elapsed

                current_time = time.time()

                # Afficher les stats une fois par seconde
                if current_time - last_print_time >= 1.0:
                    mean_time = (total_elapsed / message_count) * 1000 if message_count > 0 else 0
                    print(f"Messages envoyés: {message_count}, Temps moyen: {mean_time:.5f}ms")
                    message_count = 0
                    total_elapsed = 0.0
                    last_print_time = current_time

                # 3. Envoi binaire (beaucoup plus performant que JSON pour des matrices)
                await websocket.send_bytes(pose_bytes)

            # 4. Régulation de la boucle (Sleep pour ne pas surcharger le CPU inutilement)
            # process_duration = time.time() - start_process
            # sleep_time = max(0.0, target_frame_time - process_duration)
            #
            # await asyncio.sleep(sleep_time)

    except WebSocketDisconnect:
        print("Client déconnecté")
        player.stop()
    except Exception as e:
        print(f"Erreur inattendue : {e}")
        await websocket.close()

if __name__ == "__main__":
    import uvicorn
    # Lance le serveur sur localhost:8000
    uvicorn.run(app, host="0.0.0.0", port=8000)