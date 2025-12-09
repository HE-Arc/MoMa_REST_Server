import asyncio
from typing import Any

import websockets
import struct
import json
import time
from websockets import ServerConnection

from MoMaFkSolver.core import FastBVH
from MoMaFkSolver.player import AnimationPlayer

# --- CONFIGURATION ---
PORT = 8765
MAGIC_NUMBER = 0xBADDF00D
TARGET_FPS = 60

# --- ÉTAT GLOBAL ---
connected_clients: set[ServerConnection] = set()
player : AnimationPlayer = None

async def handler(websocket: ServerConnection):
    """
    Gère une nouvelle connexion client.
    """
    print(f"[+] Nouveau client : {websocket.remote_address}")

    # 1. Enregistrement
    connected_clients.add(websocket)

    try:
        # 2. Handshake : Envoyer les noms des os immédiatement
        # C'est ce qui permet au client TS de mapper Index -> Nom
        await websocket.send(json.dumps(player.anim.get_skeleton_definition()))

        # 3. Maintenir la connexion ouverte
        # On attend simplement que le client se déconnecte
        await websocket.wait_closed()

    except websockets.ConnectionClosed:
        pass
    finally:
        # 4. Nettoyage
        print(f"[-] Client déconnecté : {websocket.remote_address}")
        connected_clients.remove(websocket)

async def broadcast_loop():
    """
    La boucle principale du jeu / animation.
    Elle tourne à 60 FPS, met à jour le Player et envoie les données.
    """
    print("Démarrage de la boucle de broadcast...")
    frame_id = 0
    interval = 1.0 / TARGET_FPS

    # Variables pour le calcul de la moyenne
    elapsed_times = []
    last_print_time = time.perf_counter()

    while True:
        start_time = time.perf_counter()

        # A. Mise à jour de la logique d'animation
        # Calcule le nouveau temps, gère le bouclage, etc.
        player.update()

        # B. Récupération des données binaires (Matrices 4x4)
        # get_current_pose_bytes() fait l'interpolation et le FK
        pose_bytes: bytes = player.get_current_pose_bytes()

        # E. Maintien du FPS (Sleep précis)
        elapsed = time.perf_counter() - start_time
        elapsed_times.append(elapsed)

        # Afficher la moyenne une fois par seconde
        current_time = time.perf_counter()
        if current_time - last_print_time >= 1.0:
            mean_elapsed_ms = (sum(elapsed_times) / len(elapsed_times)) * 1000
            print(f"Mean frame time: {mean_elapsed_ms:.3f} ms ({len(elapsed_times)} frames)")
            elapsed_times.clear()
            last_print_time = current_time

        # S'il y a des données et des clients, on envoie
        if pose_bytes and connected_clients:
            # pose_array = player.get_current_pose()
            #
            # # print world position of every bone for debug
            # for i, mat in enumerate(pose_array):
            #     pos = mat[:3, 3]
            #     print(f"Bone {i} position: {pos}")

            # C. Construction du Header
            # Magic (4) + FrameID (4) + NumChars (4)
            # Pour l'instant on a 1 seul personnage
            header : bytes = struct.pack('<III', MAGIC_NUMBER, frame_id, 1)
            payload : bytes = header + pose_bytes

            # D. Broadcast efficace
            # websockets.broadcast est plus efficace qu'une boucle for
            websockets.broadcast(connected_clients, payload, True)

        frame_id += 1

        sleep_time = max(0, interval - elapsed)
        await asyncio.sleep(sleep_time)

async def main():
    global player

    # 1. Chargement des Assets
    print("Chargement des animations...")
    # Remplacer par votre fichier BVH ou GLTF
    # anim_data = FastGLTF("assets/character.glb", target_fps=60)
    anim_data = FastBVH("src/animations/07_01.bvh")

    if anim_data.num_frames == 0:
        print("Erreur: Aucune frame chargée !")
        return

    # 2. Init Player
    player = AnimationPlayer(anim_data)
    player.loop = True
    player.speed = 1.0
    player.play() # Lance le chronomètre interne

    print(f"Animation chargée : {len(player.get_bone_names())} os, {anim_data.duration:.2f}s")

    # 3. Démarrage du Serveur WebSocket
    # On lance le serveur en tâche de fond
    server = await websockets.serve(handler, "localhost", PORT)
    print(f"Serveur WebSocket prêt sur ws://localhost:{PORT}")

    # 4. Démarrage de la boucle d'animation
    # On utilise create_task pour que ça tourne en parallèle du serveur
    asyncio.create_task(broadcast_loop())

    # 5. Garder le programme en vie
    await asyncio.Future()  # Run forever

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Arrêt du serveur.")