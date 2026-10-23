"""Installed package entry point; source checkouts use tools/cli.py."""
from .tools.cli import main

raise SystemExit(main())
