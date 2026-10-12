"""Tiny storage layer.

USE_FIRESTORE=1 -> Firestore (data survives restarts; use this once deployed)
otherwise       -> in-memory (fine for local dev and the Day 1 infra test)
"""
import os
import uuid


class MemoryStore:
    def __init__(self):
        self.data: dict[str, dict[str, dict]] = {}

    def put(self, collection: str, doc: dict, doc_id: str | None = None) -> str:
        doc_id = doc_id or uuid.uuid4().hex[:10]
        self.data.setdefault(collection, {})[doc_id] = doc
        return doc_id

    def get(self, collection: str, doc_id: str) -> dict | None:
        return self.data.get(collection, {}).get(doc_id)

    def list(self, collection: str) -> dict[str, dict]:
        return dict(self.data.get(collection, {}))


class FirestoreStore:
    def __init__(self):
        from google.cloud import firestore

        self.db = firestore.Client()

    def put(self, collection: str, doc: dict, doc_id: str | None = None) -> str:
        ref = self.db.collection(collection).document(doc_id) if doc_id else self.db.collection(collection).document()
        ref.set(doc)
        return ref.id

    def get(self, collection: str, doc_id: str) -> dict | None:
        snap = self.db.collection(collection).document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    def list(self, collection: str) -> dict[str, dict]:
        return {d.id: d.to_dict() for d in self.db.collection(collection).stream()}


store = FirestoreStore() if os.getenv("USE_FIRESTORE") == "1" else MemoryStore()
