"""Admin CLI for the baseline backend: onboard users and grant project roles.

Usage (run from the repo root with the cloud deps installed)::

    python -m cloud.server.manage create-token alice --name "Alice" [--admin]
    python -m cloud.server.manage add-member <baseline_id> alice editor
    python -m cloud.server.manage list-projects

The database is taken from ``--db`` or ``$BASELINE_DB_URL`` (default: the local
SQLite file). ``create-token`` prints the bearer token once — store it safely; it
is only ever persisted as a SHA-256 hash.
"""

from __future__ import annotations

import argparse
import sys

from cloud.server import auth, db
from cloud.server.config import load_settings


def _session(db_url: str):
    engine = db.make_engine(db_url)
    db.create_all(engine)
    return db.make_session_factory(engine)()


def _cmd_create_token(args: argparse.Namespace) -> int:
    token = args.token or auth.new_token()
    with _session(args.db) as session:
        auth.ensure_user_with_token(session, args.user_id, args.name or args.user_id, token, args.admin)
        session.commit()
    print(f"user={args.user_id} admin={args.admin}")
    print(f"token={token}")
    return 0


def _cmd_add_member(args: argparse.Namespace) -> int:
    with _session(args.db) as session:
        if session.get(db.Project, args.baseline_id) is None:
            print(f"项目不存在：{args.baseline_id}", file=sys.stderr)
            return 1
        auth.add_member(session, args.baseline_id, args.user_id, args.role)
        session.commit()
    print(f"granted {args.role} on {args.baseline_id} to {args.user_id}")
    return 0


def _cmd_list_projects(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    with _session(args.db) as session:
        for project in session.scalars(select(db.Project).order_by(db.Project.baseline_id)):
            print(f"{project.baseline_id}\t{project.name}\tactive={project.active_version}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=load_settings().db_url, help="Database URL (or $BASELINE_DB_URL).")
    sub = parser.add_subparsers(dest="command", required=True)

    ct = sub.add_parser("create-token", help="Create/rotate a user + bearer token.")
    ct.add_argument("user_id")
    ct.add_argument("--name", default="")
    ct.add_argument("--admin", action="store_true", help="Global admin (bypasses membership).")
    ct.add_argument("--token", default="", help="Use a specific token instead of a random one.")
    ct.set_defaults(func=_cmd_create_token)

    am = sub.add_parser("add-member", help="Grant a project role to a user.")
    am.add_argument("baseline_id")
    am.add_argument("user_id")
    am.add_argument("role", choices=("reviewer", "editor", "admin"))
    am.set_defaults(func=_cmd_add_member)

    lp = sub.add_parser("list-projects", help="List projects in the database.")
    lp.set_defaults(func=_cmd_list_projects)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
