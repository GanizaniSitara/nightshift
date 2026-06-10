"""Run the watchdog as a plain module: ``python -m nightshift.watchdog [once|loop|status]``."""

from .watchdog import main

raise SystemExit(main())
