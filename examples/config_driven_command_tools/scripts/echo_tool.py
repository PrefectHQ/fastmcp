#!/usr/bin/env python3
"""Echo script: prints --msg to stdout and exits 0. Used for parameter passthrough tests."""

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--msg", required=True, help="Message to echo")
    args = parser.parse_args()
    print(args.msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
