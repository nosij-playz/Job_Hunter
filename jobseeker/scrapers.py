"""Compatibility shim for legacy scraper imports.

The scraper modules now live in the jobseeker.scrapper package.
"""

from jobseeker.scrapper.scrapers import *  # noqa: F401,F403
from jobseeker.scrapper.scrapers_discovery import *  # noqa: F401,F403
