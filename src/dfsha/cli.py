from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .client import DFShaClient, DFShaClientError


def _client(args: argparse.Namespace) -> DFShaClient:
    endpoints = [value.strip() for value in args.mds.split(",") if value.strip()]
    verify: str | bool = args.ca if args.ca else False
    client = DFShaClient(endpoints, verify=verify)
    client.login(args.user, args.password)
    return client


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dfsha", description="Cliente de DFSha")
    parser.add_argument("--mds", default=os.getenv("DFSHA_MDS_URLS", "http://127.0.0.1:8001,http://127.0.0.1:8002,http://127.0.0.1:8003"))
    parser.add_argument("--user", default=os.getenv("DFSHA_USER", "demo"))
    parser.add_argument("--password", default=os.getenv("DFSHA_PASSWORD", "demo-change-me"))
    parser.add_argument("--ca", default=os.getenv("DFSHA_TLS_CA"))
    commands = parser.add_subparsers(dest="command", required=True)

    ls = commands.add_parser("ls")
    ls.add_argument("path", nargs="?", default="/")
    for name in ("mkdir", "rmdir", "rm", "stat"):
        item = commands.add_parser(name)
        item.add_argument("path")
    put = commands.add_parser("put")
    put.add_argument("local")
    put.add_argument("remote")
    get = commands.add_parser("get")
    get.add_argument("remote")
    get.add_argument("local")
    read = commands.add_parser("read")
    read.add_argument("remote")
    read.add_argument("offset", type=int)
    read.add_argument("length", type=int)
    write = commands.add_parser("write")
    write.add_argument("remote")
    write.add_argument("offset", type=int)
    write.add_argument("data")
    write.add_argument("--create", action="store_true")
    append = commands.add_parser("append")
    append.add_argument("remote")
    append.add_argument("data")
    lock = commands.add_parser("lock")
    lock.add_argument("remote")
    lock.add_argument("--hold", type=float, default=5.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        client = _client(args)
        if args.command == "ls":
            result = client.ls(args.path)
        elif args.command == "mkdir":
            result = client.mkdir(args.path)
        elif args.command == "rmdir":
            result = client.rmdir(args.path)
        elif args.command == "rm":
            result = client.rm(args.path)
        elif args.command == "stat":
            result = client.stat(args.path)
        elif args.command == "put":
            result = client.put(args.local, args.remote)
        elif args.command == "get":
            result = client.get(args.remote, args.local)
        elif args.command == "read":
            sys.stdout.buffer.write(client.read(args.remote, args.offset, args.length))
            return
        elif args.command == "write":
            result = client.write(args.remote, args.offset, args.data.encode(), create=args.create)
        elif args.command == "append":
            result = client.append(args.remote, args.data.encode())
        elif args.command == "lock":
            session = client.lock(args.remote)
            try:
                print(json.dumps({"locked": args.remote, "handle": session["handle"]}, indent=2))
                time.sleep(args.hold)
            finally:
                result = client.close_write(session)
        else:
            raise AssertionError(args.command)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    except DFShaClientError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()

