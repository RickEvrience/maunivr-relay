# relay.py
import os, asyncio, json, websockets
from websockets.exceptions import ConnectionClosed

# ROOMS: { room_code: { peer_id: websocket } }
ROOMS: dict[str, dict[str, websockets.WebSocketServerProtocol]] = {}

# simpele limieten tegen misbruik
MAX_PEERS_PER_ROOM = 64
MAX_AUDIO_SAMPLES = 48000 * 2  # max 2s per bericht
MAX_MSG_BYTES = 512 * 1024     # 512 KB

async def register(room: str, peer: str, ws):
    room_peers = ROOMS.setdefault(room, {})
    if len(room_peers) >= MAX_PEERS_PER_ROOM:
        await ws.close(code=4000, reason="room full")
        return False
    room_peers[peer] = ws
    print(f"[join] room={room} peer={peer} peers={len(room_peers)}")
    # optioneel: stuur een welkom/peerlijst
    await ws.send(json.dumps({"type":"joined","room":room,"peer":peer,"peers":list(room_peers.keys())}))
    return True

def unregister(room: str, peer: str):
    room_peers = ROOMS.get(room)
    if not room_peers: return
    if peer in room_peers:
        room_peers.pop(peer, None)
        print(f"[leave] room={room} peer={peer} peers={len(room_peers)}")
    if not room_peers:
        ROOMS.pop(room, None)

async def relay_to_room(room: str, sender_peer: str, payload: str):
    """Broadcast JSON string 'payload' naar alle peers in room behalve afzender."""
    room_peers = ROOMS.get(room, {})
    coros = []
    for p, cws in list(room_peers.items()):
        if p == sender_peer: 
            continue
        if cws.closed:
            continue
        coros.append(cws.send(payload))
    if coros:
        await asyncio.gather(*coros, return_exceptions=True)

async def handler(ws, path):
    # 1) wacht op eerste bericht: type=join
    room = peer = None
    try:
        first = await asyncio.wait_for(ws.recv(), timeout=10)
        if isinstance(first, bytes):
            # Onze client stuurt tekstframes
            await ws.close(code=1003, reason="text frames only")
            return

        if len(first.encode("utf-8")) > MAX_MSG_BYTES:
            await ws.close(code=1009, reason="message too large")
            return

        try:
            obj = json.loads(first)
        except json.JSONDecodeError:
            await ws.close(code=1007, reason="invalid json")
            return

        if not (isinstance(obj, dict) and obj.get("type") == "join"):
            await ws.close(code=1002, reason="expected type=join")
            return

        room = str(obj.get("room") or "").strip()
        peer = str(obj.get("peer") or "").strip()
        if not room or not peer:
            await ws.close(code=1008, reason="room/peer required")
            return

        ok = await register(room, peer, ws)
        if not ok:
            return

        # 2) main loop: ontvang berichten, valideer, relay
        async for msg in ws:
            # we verwachten text/json
            if isinstance(msg, bytes):
                # hou ’t simpel: drop binary
                continue

            if len(msg.encode("utf-8")) > MAX_MSG_BYTES:
                # te groot -> drop
                continue

            try:
                o = json.loads(msg)
            except json.JSONDecodeError:
                continue

            # verwacht audio berichten
            if not isinstance(o, dict):
                continue
            if o.get("type") != "audio":
                # je kunt hier ook "ping" etc. toevoegen
                continue
            if o.get("room") != room:
                continue
            if o.get("from") != peer:
                continue

            # very light sanity checks
            samples = int(o.get("samples") or 0)
            if samples <= 0 or samples > MAX_AUDIO_SAMPLES:
                continue
            b64 = o.get("pcm")
            if not isinstance(b64, str) or len(b64) > (MAX_MSG_BYTES // 2):
                continue

            # Relay het originele JSON door (niet her-serialiseren is prima)
            await relay_to_room(room, peer, msg)

    except (asyncio.TimeoutError, ConnectionClosed):
        pass
    except Exception as e:
        print(f"[error] {e}")
    finally:
        if room and peer:
            unregister(room, peer)

async def main():
    port = int(os.environ.get("PORT", "8080"))
    print(f"Starting WebSocket relay on port {port}")
    async with websockets.serve(
        handler, "0.0.0.0", port,
        ping_interval=20,  # hou idle connecties in leven
        ping_timeout=20,
        max_size=MAX_MSG_BYTES
    ):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
