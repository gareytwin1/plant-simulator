"""
Per-session plant registry.

main.py used to hold one module-global compressor and pump, so every
browser shared the same plant and one visitor's actions were visible to
everyone else's. A Session bundles a fresh device instance of each kind;
SessionRegistry creates, looks up and tears one down by session id, so
concurrent browsers never see each other's state.
"""

from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump


class Session:
    def __init__(self):
        self.compressor = GasCompressor()
        self.pump = CentrifugalPump()


class SessionRegistry:
    def __init__(self):
        self._sessions = {}

    def create(self, session_id):
        session = Session()
        self._sessions[session_id] = session
        return session

    def get(self, session_id):
        return self._sessions.get(session_id)

    def get_or_create(self, session_id):
        session = self._sessions.get(session_id)
        if session is None:
            session = self.create(session_id)
        return session

    def end(self, session_id):
        self._sessions.pop(session_id, None)

    def __len__(self):
        return len(self._sessions)
