from __future__ import annotations

import click

from corpochain.util.config import load_config
from corpochain.util.service_groups import all_groups


@click.command("start", short_help="Start service groups")
@click.option("-r", "--restart", is_flag=True, type=bool, help="Restart running services")
@click.argument("group", type=click.Choice(list(all_groups())), nargs=-1, required=True)
@click.pass_context
def start_cmd(ctx: click.Context, restart: bool, group: str) -> None:
    import asyncio

    from corpochain.cmds.beta_funcs import warn_if_beta_enabled

    from .start_funcs import async_start

    root_path = ctx.obj["root_path"]
    config = load_config(root_path, "config.yaml")
    warn_if_beta_enabled(config)
    asyncio.run(async_start(root_path, config, group, restart))
