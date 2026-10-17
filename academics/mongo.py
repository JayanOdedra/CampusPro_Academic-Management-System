"""
academics/mongo.py
------------------
Single place for all MongoDB connectivity.

Design decisions:
- Singleton pattern: MongoClient is created once at module import time.
  Creating a new client per request is expensive; one client handles a
  connection pool internally.
- Lazy initialisation: if MONGO_URI is missing or Mongo is down, we set
  _db = None and every helper returns None gracefully.
- Every public function is wrapped in try/except so a Mongo failure
  NEVER propagates to the user — it is only logged to the Python logger.
"""

import os
import logging

# Standard Python logger — writes to console (or wherever Django routes logs)
logger = logging.getLogger(__name__)

# Module-level variable; None means Mongo is unavailable
_db = None


def _connect():
    """
    Attempt to connect to MongoDB using MONGO_URI from the environment.
    Called once at module import. Sets the module-level _db variable.
    serverSelectionTimeoutMS=3000 means pymongo gives up after 3 seconds
    instead of hanging the request thread indefinitely.
    """
    global _db
    try:
        from pymongo import MongoClient, DESCENDING, ASCENDING
        uri  = os.environ.get('MONGO_URI', 'mongodb://localhost:27017')
        name = os.environ.get('MONGO_DB_NAME', 'campuspro')
        # serverSelectionTimeoutMS: how long to wait before raising an error
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        # ping the server to confirm the connection is alive
        client.admin.command('ping')
        _db = client[name]
        logger.info("MongoDB connected: %s / %s", uri, name)
        _create_indexes()
    except Exception as exc:
        print(">>> MONGO ERROR:", repr(exc), flush=True)
        # Log the error but do NOT re-raise — the app continues without Mongo
        logger.warning("MongoDB unavailable: %s", exc)
        _db = None


def _create_indexes():
    """
    Create indexes on startup so queries are fast.
    Called only when the connection succeeds.
    background=True means index creation doesn't block other operations.
    """
    try:
        from pymongo import DESCENDING, ASCENDING
        # audit_logs: sort by timestamp descending (newest first) and filter by action
        _db['audit_logs'].create_index([('timestamp', DESCENDING)], background=True)
        _db['audit_logs'].create_index([('action', ASCENDING)],     background=True)
        _db['audit_logs'].create_index([('username', ASCENDING)],   background=True)
        # notices: sort by created_at descending
        _db['notices'].create_index([('created_at', DESCENDING)],   background=True)
        _db['notices'].create_index([('audience', ASCENDING)],      background=True)
    except Exception as exc:
        logger.warning("MongoDB index creation failed: %s", exc)


# Run the connection attempt when this module is first imported
_connect()


def get_audit_collection():
    """
    Return the audit_logs collection, or None if Mongo is unavailable.
    Callers must check for None before using the result.
    """
    if _db is None:
        return None
    try:
        return _db['audit_logs']
    except Exception as exc:
        logger.warning("get_audit_collection error: %s", exc)
        return None


def get_notices_collection():
    """
    Return the notices collection, or None if Mongo is unavailable.
    """
    print(">>> MONGO DEBUG: _db is", _db, flush=True)
    if _db is None:
        return None
    try:
        return _db['notices']
    except Exception as exc:
        logger.warning("get_notices_collection error: %s", exc)
        return None
