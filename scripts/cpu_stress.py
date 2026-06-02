#!/usr/bin/env python3
"""
scripts/cpu_stress.py — Stress test to consume 100% CPU on all cores.

Policy & Technical Details:
- Spawns CPU-heavy worker processes matching the host's total CPU core count.
- Each worker runs a tight arithmetic loop to maximize CPU utilization to 100%.
- Terminates automatically after a specified duration or via Ctrl+C.

Writer: Santa, Wiseyak
Date: 2026-06-02
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import time


def cpu_worker() -> None:
    # A tight loop performing math operations to maximize CPU core usage
    while True:
        _ = 999999 * 999999


def run_stress(duration: int) -> None:
    cores = os.cpu_count() or 2
    processes = []
    for _ in range(cores):
        p = multiprocessing.Process(target=cpu_worker, daemon=True)
        p.start()
        processes.append(p)

    try:
        time.sleep(duration)
    finally:
        for p in processes:
            p.terminate()
            p.join()


def main() -> None:
    duration = 30  # Default duration in seconds
    if len(sys.argv) > 1:
        try:
            duration = int(sys.argv[1])
        except ValueError:
            print(
                "Usage: python scripts/cpu_stress.py [duration_seconds]",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"Starting CPU stress test for {duration} seconds...")
    print("Open 'htop' in another terminal to observe all CPU cores at 100%.")
    print("Press Ctrl+C to terminate the test early.")

    try:
        run_stress(duration)
        print("\nStress test duration finished. Stopping workers...")
    except KeyboardInterrupt:
        print("\nInterrupted by user. Stopping workers...")
    print("All processes stopped successfully. CPU usage will return to normal.")


if __name__ == "__main__":
    main()
