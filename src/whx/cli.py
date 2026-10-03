"""WHX command-line application."""

import click

from whx.commands.db import db


@click.group()
def cli() -> None:
    """WHX - migrate HoursTracker CSV time entries to WorkingHours backups."""


cli.add_command(db)
