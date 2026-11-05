"""Persistent outbound connection from the home box to Render.

The home box is never exposed inbound. It dials out, authenticates with its client key, and
keeps the socket open. Render pushes bet lists and scrape jobs; the home box pushes reports,
tips and escalation outcomes. Every message carries a ``msg_id``; both sides ignore replays.
"""
