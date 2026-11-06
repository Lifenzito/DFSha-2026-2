from __future__ import annotations

import argparse
import csv
import os
import statistics
import time
from pathlib import Path

from dfsha.client import DFShaClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mds", required=True, help="lista de MDS separada por comas")
    parser.add_argument("--user", default="demo")
    parser.add_argument("--password", default="demo-change-me")
    parser.add_argument("--sizes", default="1048576,8388608")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", default="evidence/benchmark.csv")
    args = parser.parse_args()

    client = DFShaClient(args.mds.split(","))
    client.login(args.user, args.password)
    try:
        client.mkdir("/bench")
    except Exception:
        pass

    rows = []
    for size in [int(value) for value in args.sizes.split(",")]:
        put_times = []
        get_times = []
        payload = os.urandom(size)
        for repetition in range(args.repetitions):
            remote = f"/bench/{size}-{repetition}.bin"
            started = time.perf_counter()
            client.put_bytes(payload, remote)
            put_times.append(time.perf_counter() - started)
            started = time.perf_counter()
            restored = client.get_bytes(remote)
            get_times.append(time.perf_counter() - started)
            assert restored == payload
        rows.append(
            {
                "size_bytes": size,
                "repetitions": args.repetitions,
                "put_median_s": statistics.median(put_times),
                "get_median_s": statistics.median(get_times),
                "put_mib_s": size / statistics.median(put_times) / (1024 * 1024),
                "get_mib_s": size / statistics.median(get_times) / (1024 * 1024),
            }
        )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(output)


if __name__ == "__main__":
    main()

