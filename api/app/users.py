"""The accounts, in code: who can log in and to which tools.

Each entry is (username, bcrypt hash of the password, grant). The grant is
"*" (every tool) or tools joined by "+", as in AUTH_USERS. The repo is
public, so only the HASH is kept here, never the password. Make one with:

    python -c "import bcrypt; print(bcrypt.hashpw(b'the-password', bcrypt.gensalt(12)).decode())"

Seeded on every startup (security.seed_coded_users): a missing user is
created, and an existing user's grant is set to the one here. A password
is set only when the user is created; changing a hash here does not change
the live password of a user who already exists.

Users created earlier from the AUTH_USERS env var stay in the database
whether or not they are listed here, so that variable can be removed from
Render without locking anyone out.
"""

USERS: list[tuple[str, str, str]] = [
    ("filo", "$2b$12$azhBuLX.HApJY0ckgocn..jVyUrUG4abPMLF89QSXvXw3ZEDNOxP2", "*"),
]
