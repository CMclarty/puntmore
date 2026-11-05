"""Persistent outbound connection from the Hub to Render.

The Hub is never exposed inbound. It dials out, authenticates with its client key, and
keeps the socket open. Render pushes bet lists and scrape jobs; the Hub pushes reports,
tips and escalation outcomes. Every message carries a ``msg_id``; both sides ignore replays.
"""
