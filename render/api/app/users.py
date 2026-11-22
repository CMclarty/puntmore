"""The accounts, in code: who can log in and to which tools.

Each entry is (username, bcrypt hash of the password, grant). The grant is
"*" (every tool) or tools joined by "+", as in AUTH_USERS. The repo is
public, so only the HASH is kept here, never the password. Make one with:

    python -c "import bcrypt; print(bcrypt.hashpw(b'the-password', bcrypt.gensalt(12)).decode())"

Seeded on every startup (security.seed_coded_users): a missing user is
created, and an existing user's grant is set to the one here (and their
name to this spelling, matched ignoring case). A password
is set only when the user is created; changing a hash here does not change
the live password of a user who already exists.

Users created earlier from the AUTH_USERS env var stay in the database
whether or not they are listed here, so that variable can be removed from
Render without locking anyone out.

Login refuses a site holding none of the user's tools: a grant of public
tools only (cards, hills) signs in on the main domain, not on Me.
"""

USERS: list[tuple[str, str, str]] = [
    ("EV farmer", "$2b$12$rocfhnX2xSAb.PRPxCd5Nel9Gn2i5AfiLhbKNVnCNJls.A2ma00.W", "cards+hills"),
]

# Accounts taken away. Each name's row is deleted on every startup
# (security.remove_coded_users, matched ignoring case, after both seedings),
# which ends its sessions at once: a session is checked against the row on
# every request. Taking a line out of USERS alone leaves the account live.
REMOVED: list[str] = ["Filo"]
