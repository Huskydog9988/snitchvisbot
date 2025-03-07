import inspect
import traceback

from discord import Client as _Client, Intents

from command import Command
import db
import config

class Client(_Client):
    def __init__(self, *args, **kwargs):
        intents = Intents.default()
        intents.message_content = True

        super().__init__(*args, **kwargs, intents=intents)

        self.default_prefix = config.DEFAULT_PREFIX
        # guild id to prefix. cache snitch prefix to avoid unecessary db hits
        self.prefixes = {}
        self.commands = []
        self.command_log_channel = None
        self.join_log_channel = None
        self.error_log_channel = None
        self.livemap_log_category = None

        # collect all registered commands
        for func in inspect.getmembers(self, predicate=inspect.ismethod):
            (_func_name, func) = func
            if not hasattr(func, "_is_command"):
                continue

            command = Command(func, func._name, func._args, func._help,
                func._help_short, func._permissions, func._use_prefix,
                func._parse)
            self.commands.append(command)

            for name in func._aliases:
                command = Command(func, name, func._args, func._help,
                    func._help_short, func._permissions, func._use_prefix,
                    func._parse, alias=True)
                self.commands.append(command)

    async def on_ready(self):
        # convert to discord object once we're connected to discord
        if config.COMMAND_LOG_CHANNEL:
            self.command_log_channel = self.get_channel(config.COMMAND_LOG_CHANNEL)
        if config.JOIN_LOG_CHANNEL:
            self.join_log_channel = self.get_channel(config.JOIN_LOG_CHANNEL)
        if config.ERROR_LOG_CHANNEL:
            self.error_log_channel = self.get_channel(config.ERROR_LOG_CHANNEL)
        if config.LIVEMAP_LOG_CATEGORY:
            # categories are a special type of channel
            self.livemap_log_category = self.get_channel(config.LIVEMAP_LOG_CATEGORY)

    async def on_guild_join(self, guild):
        if self.join_log_channel:
            await self.join_log_channel.send(f"Joined new guild `{guild.name}` "
                f"/ `{guild.id}`")
        db.create_new_guild(guild.id)

    async def on_error(self, event_method, *args, **kwargs):
        await super().on_error(event_method, *args, **kwargs)

        if self.error_log_channel:
            err = traceback.format_exc()
            await self.error_log_channel.send(f"Ignoring exception in "
                f"{event_method}: \n```\n{err}\n```")


    async def on_message(self, message):
        await self.maybe_handle_command(message, message.content)

    async def maybe_handle_command(self, message, content, *,
        include_custom=True, override_testing_ignore=False
    ):
        author = message.author
        guild = message.guild

        # only respond to messages from whitelisted guilds if testing, to avoid
        # responding from commands from actual users
        if not override_testing_ignore:
            if config.TESTING and guild.id not in config.TESTING_GUILDS:
                return

        if guild.id not in self.prefixes:
            prefix = db.get_snitch_prefix(guild.id)
            # fall back to default prefix if no prefix specified
            if prefix is None:
                self.prefixes[guild.id] = self.default_prefix
        prefix = self.prefixes[guild.id]

        custom_commands = db.get_commands(guild.id)

        commands = self.commands
        if include_custom:
            # make sure we handle custom commands first, so arguments to aliases
            # get passed forward to the actual commands.
            commands = custom_commands + commands

        for command in commands:
            if not self.command_matches(guild.id, command, content):
                continue

            if command.use_prefix:
                command_name = prefix + command.name

            # don't log commands by the author, gets annoying for testing
            if author.id != config.AUTHOR_ID and self.command_log_channel:
                await self.command_log_channel.send(f"[`{guild.name}`] "
                    f"[{author.mention} / `{author.name}`] `{content}`")

            # also strip any whitespace, particularly after the command name
            args = content.removeprefix(command_name).strip()

            if isinstance(command, Command):
                await command.invoke(message, args)
            else:
                # forward any alias args to the actual command
                command_text = f"{command.command_text} {args}"
                # recurse on the aliased command text. Ensure we avoid infinite
                # recursion by not considering custom commands on this recurse.
                await self.maybe_handle_command(message, command_text,
                    include_custom=False)

            # only process one command per message, to avoid recursion on
            # custom commands
            return

    def command_matches(self, guild_id, command, content):
        if guild_id not in self.prefixes:
            prefix = db.get_snitch_prefix(guild_id)
            # fall back to default prefix if no prefix specified
            if prefix is None:
                self.prefixes[guild_id] = self.default_prefix
        prefix = self.prefixes[guild_id]

        command_name = command.name
        # some commands don't respect the prefix at all, eg
        # snitchvissetprefix
        if command.use_prefix:
            command_name = prefix + command.name

        # avoid eg .r matching .render by requiring the input to either match
        # exactly, or match the command name with a space.
        return (
            content.startswith(command_name + " ") or
            content == command_name
        )
