import json

class WSHub:
    def __init__(self):
        self.clients = set()
        self.history = []
    async def connect(self, ws):
        await ws.accept()
        self.clients.add(ws)
    def disconnect(self, ws):
        self.clients.remove(ws)
    async def broadcast(self, data):
        self.history.append(data)
        for client in self.clients:
            await client.send_text(json.dumps(data))

hub = WSHub()
