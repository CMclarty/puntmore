"""Minimal auth for a small trusted syndicate: bcrypt + signed session cookie.

No registration / reset flows. Users are seeded from Settings.auth_users on
startup. require_user is the single guard applied to every data router.
"""
import bcrypt
from fastapi import HTTPException, Request
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from .database import engine
from .models import User


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def require_user(request: Request) -> str:
    """FastAPI dependency: returns the session username or raises 401.

    Overridable in tests via app.dependency_overrides[require_user].
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def parse_auth_users(raw: str) -> list[tuple[str, str]]:
    """Parse "u1:p1,u2:p2" → [("u1","p1"), ("u2","p2")]. Tolerant of whitespace."""
    pairs = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        username, password = chunk.split(":", 1)
        username, password = username.strip(), password.strip()
        if username and password:
            pairs.append((username, password))
    return pairs


def seed_users(db: Session, raw: str) -> int:
    """Create any users from auth_users that don't already exist.

    Existing users are never modified (change a password by deleting the row).
    Returns the number of users created.
    """
    created = 0
    for username, password in parse_auth_users(raw):
        exists = db.query(User).filter(User.username == username).first()
        if exists:
            continue
        db.add(User(username=username, password_hash=hash_password(password)))
        created += 1
    db.commit()
    return created


def ensure_schema() -> None:
    """Idempotent SQLite migrations. Safe to run on every startup."""
    from .models import Base  # local import avoids circular at module level

    inspector = inspect(engine)
    tables = inspector.get_table_names()

    # R1 migration: old schema had sets/versions/matches + template coupons.
    # Detect by presence of 'sets' or 'versions' (new schema has neither).
    if "sets" in tables or "versions" in tables:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS matches"))
            conn.execute(text("DROP TABLE IF EXISTS versions"))
            conn.execute(text("DROP TABLE IF EXISTS sets"))
            conn.execute(text("DROP TABLE IF EXISTS coupons"))
            conn.execute(text("DROP TABLE IF EXISTS legs"))
        Base.metadata.create_all(bind=engine)
        return  # fresh schema created; nothing else to migrate

    # Old coupons table with sort_order (template model) but without sets/versions.
    if "coupons" in tables:
        cols = {c["name"] for c in inspector.get_columns("coupons")}
        if "sort_order" in cols:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS coupons"))
                conn.execute(text("DROP TABLE IF EXISTS legs"))
            Base.metadata.create_all(bind=engine)
