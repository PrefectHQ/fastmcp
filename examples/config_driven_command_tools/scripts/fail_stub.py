#!/usr/bin/env python3
"""Fail stub: writes to stderr and exits with code 1. Used for error path tests."""

import sys

if __name__ == "__main__":
    print("Intentional failure for testing", file=sys.stderr)
    sys.exit(1)
