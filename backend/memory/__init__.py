"""Provide the Profile-owned Memory module used by Main Agent.

Composition imports concrete storage and extraction adapters directly. Conversation buffering and
pending archives belong to Main Agent Context; this package owns long-term ingestion and recall only.
"""
