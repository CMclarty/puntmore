"""API client keys — create, list, revoke.

    python -m app.apikeys create <name> --tools paddy,value   # prints the key ONCE
    python -m app.apikeys list
    python -m app.apikeys revoke <name>

Run from api/ (locally) or /app (Render shell) — it uses the app's own
DATABASE_URL. Only the key's sha256 is stored; the key is printed once on
create and nowhere else, so copy it then. Revoking disables the key at once
(the row is kept as the record); to rotate, create a new client and revoke
the old one.

--tools takes grant names (security.ALL_GRANTS) or "*", comma- or
plus-separated. The page names are accepted too: paddy = cards,
365 = accabonus + goalscorers.
"""
import argparse
import sys

from .database import SessionLocal, engine
from .models import ApiClient, Base
from .security import ALL_GRANTS, generate_api_key, hash_api_key

# The tools' page names → the grant names they keep for compatibility.
ALIASES = {
    "paddy": ("cards",),
    "365": ("accabonus", "goalscorers"),
    "scorers": ("goalscorers",),
}


def parse_tools(raw: str) -> str:
    """"paddy,value" → "cards+value". Raises ValueError on an unknown name."""
    names = [t.strip().lower() for t in raw.replace("+", ",").split(",") if t.strip()]
    if not names:
        raise ValueError("--tools is empty")
    if "*" in names:
        if len(names) > 1:
            raise ValueError('"*" already grants everything; give it alone')
        return "*"
    grants: set[str] = set()
    for name in names:
        if name in ALIASES:
            grants.update(ALIASES[name])
        elif name in ALL_GRANTS:
            grants.add(name)
        else:
            raise ValueError(
                f"unknown tool {name!r} — one of: {', '.join(ALL_GRANTS + tuple(ALIASES))}, or *"
            )
    return "+".join(sorted(grants))


def create(db, name: str, tools: str) -> tuple[ApiClient, str]:
    """A new client and its key. The key is returned ONCE and never stored."""
    name = name.strip()
    if not name:
        raise ValueError("name is empty")
    if db.query(ApiClient).filter(ApiClient.name == name).first() is not None:
        raise ValueError(f"a client named {name!r} already exists (revoked ones too)")
    key = generate_api_key()
    client = ApiClient(name=name, key_hash=hash_api_key(key),
                       permissions=parse_tools(tools), enabled=True)
    db.add(client)
    db.commit()
    db.refresh(client)
    return client, key


def revoke(db, name: str) -> ApiClient:
    client = db.query(ApiClient).filter(ApiClient.name == name).first()
    if client is None:
        raise ValueError(f"no client named {name!r}")
    client.enabled = False
    db.commit()
    return client


def list_clients(db) -> list[ApiClient]:
    return db.query(ApiClient).order_by(ApiClient.created_at, ApiClient.id).all()


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.apikeys", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="create a client and print its key once")
    c.add_argument("name")
    c.add_argument("--tools", required=True, help='e.g. "paddy,value", "value", or "*"')
    sub.add_parser("list", help="list clients (never their keys)")
    r = sub.add_parser("revoke", help="disable a client's key")
    r.add_argument("name")
    args = parser.parse_args(argv)

    # The table may not exist yet if the app has never booted on this database.
    Base.metadata.create_all(bind=engine, tables=[ApiClient.__table__])
    db = SessionLocal()
    try:
        if args.cmd == "create":
            client, key = create(db, args.name, args.tools)
            print(f"Created API client {client.name!r} with grant {client.permissions!r}.")
            print("Key (shown ONCE — it is stored only as a hash):")
            print(key)
        elif args.cmd == "list":
            rows = list_clients(db)
            if not rows:
                print("No API clients.")
            for cl in rows:
                state = "enabled" if cl.enabled else "REVOKED"
                print(f"{cl.name}\t{cl.permissions}\t{state}\t"
                      f"created {_fmt(cl.created_at)}\tlast used {_fmt(cl.last_used_at)}")
        elif args.cmd == "revoke":
            client = revoke(db, args.name)
            print(f"Revoked {client.name!r}.")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
