import os, asyncio, websockets, json

CLIENTS = {}

async def handler(ws, path):
    data = await ws.recv()
    msg = json.loads(data)
    room = msg["room"]
    role = msg["role"]
    CLIENTS.setdefault(room, {})[role] = ws
    print(f"{role} joined room {room}")

    try:
        async for packet in ws:
            for r, cws in CLIENTS[room].items():
                if cws != ws:
                    await cws.send(packet)
    except:
        pass
    finally:
        CLIENTS[room].pop(role, None)

async def main():
    port = int(os.environ.get("PORT", 8765))
    print(f"Starting WebSocket relay on port {port}")
    async with websockets.serve(handler, "0.0.0.0", port):
        await asyncio.Future()  # draai oneindig

if __name__ == "__main__":
    asyncio.run(main())
