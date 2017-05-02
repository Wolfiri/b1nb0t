import gevent
import gc
import sys
import time
import pprint

from collections import Counter, defaultdict

from disco.bot import Plugin, CommandLevels
from disco.util.snowflake import to_datetime
from disco.types.permissions import Permissions
from disco.types.message import MessageTable
from disco.types.user import GameType, Status, Game
from disco.util.functional import take

PY_CODE_BLOCK = '```py\n{}\n```'


def sizeof_fmt(num, suffix='B'):
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


class UtilPlugin(Plugin):
    def load(self, ctx):
        super(UtilPlugin, self).load(ctx)
        self.event_counter = ctx.get('event_counter') or defaultdict(int)
        self.startup = ctx.get('startup') or time.time()

    def unload(self, ctx):
        ctx['event_counter'] = self.event_counter
        ctx['startup'] = self.startup
        super(UtilPlugin, self).unload(ctx)

    @Plugin.listen('')
    def on_any_event(self, event):
        self.event_counter[event.__class__.__name__] += 1

    @Plugin.command('info', '<user:user>')
    def command_info(self, event, user):
        lines = []
        lines.append('**ID:** {}'.format(user.id))
        lines.append('**Username:** {}'.format(user.username))
        lines.append('**Discriminator:** {}'.format(user.discriminator))

        if event.guild:
            member = event.guild.get_member(user)
            if member:
                if member.nick:
                    lines.append('**Nickname:** {}'.format(member.nick))

                lines.append('**Joined At:** {}'.format(member.joined_at))

        lines.append('**Creation Date:** {}'.format(to_datetime(user.id)))
        event.msg.reply('\n'.join(lines))

    @Plugin.command('clear', group='status', level=CommandLevels.TRUSTED)
    def command_clear(self, event):
        self.client.update_presence(status=Status.ONLINE)
        event.msg.reply('Ok, status cleared')

    @Plugin.command('plug', '<stream:str> <msg:str...>', group='status', level=CommandLevels.TRUSTED)
    def command_plug(self, event, stream, msg):
        self.client.update_presence(
            game=Game(
                type=GameType.STREAMING,
                name=msg,
                url='http://twitch.tv/{}'.format(stream)),
            status=Status.ONLINE
        )
        event.msg.reply("Ok, started plugging {}'s stream!".format(stream))

    @Plugin.command('get', '<module:str> <key:str>', group='config', level=CommandLevels.OWNER)
    def config_get(self, event, module, key):
        obj = None

        if key in ['token']:
            return

        if module == 'client' and hasattr(self.client.config, key):
            obj = getattr(self.client.config, key)
        elif module == 'bot' and hasattr(self.bot.config, key):
            obj = getattr(self.bot.config, key)
        elif module in self.bot.plugins:
            if hasattr(self.bot.plugins[module].config, key):
                obj = getattr(self.bot.plugins[module].config, key)

        if not obj:
            event.msg.reply('Failed to find configuration key')
        else:
            event.msg.reply('`{} = {}`'.format(key, repr(obj)))

    @Plugin.command('events', '[size:int]', group='debug', level=CommandLevels.TRUSTED)
    def debug_events(self, event, size=50):
        name = self.name

        def get_event_count(bot):
            return bot.plugins[name].event_counter

        obj = sum(map(Counter, self.bot.shards.all(get_event_count).values()), Counter())

        table = MessageTable()
        table.set_header('Event', 'Count', 'Per Minute', 'Per Second')

        runtime = max(1, int((time.time() - self.startup) / 60))

        for name, count in sorted(obj.items(), key=lambda i: i[1], reverse=True)[:size]:
            table.add(name, count, count / runtime, (count / runtime) / 60)

        event.msg.reply(table.compile())

    @Plugin.command('shards', group='debug', level=CommandLevels.TRUSTED)
    def debug_shards(self, event):
        msg = event.msg.reply('One moment, collecting shard information...')

        table = MessageTable()
        table.set_header('Shard', 'Guilds', 'Channels', 'Users')

        guilds_uniq = set()
        channels_uniq = set()
        users_uniq = set()

        guilds = self.bot.shards.all(lambda bot: list(bot.client.state.guilds.keys()))
        channels = self.bot.shards.all(lambda bot: list(bot.client.state.channels.keys()))
        users = self.bot.shards.all(lambda bot: list(bot.client.state.users.keys()))

        for shard in self.bot.shards.keys():
            table.add(shard, len(guilds[shard]), len(channels[shard]), len(users[shard]))
            guilds_uniq |= set(guilds[shard])
            channels_uniq |= set(channels[shard])
            users_uniq |= set(users[shard])

        msg.edit(table.compile() + '\n' + 'Unique Guilds: `{}`, Unique Channels: `{}`, Unique Users: `{}`'.format(
            len(guilds_uniq),
            len(channels_uniq),
            len(users_uniq),
        ))

    @Plugin.command('status', group='debug', level=CommandLevels.TRUSTED)
    def debug_status(self, event):
        table = MessageTable()
        table.set_header('Metric', 'Value')
        table.add('Guilds', len(self.state.guilds))
        table.add('Channels', len(self.state.channels))
        table.add('Users', len(self.state.users))

        try:
            import psutil
            memory = psutil.Process().memory_info()

            table.add('Memory RSS', sizeof_fmt(memory.rss))
            table.add('Memory VMS', sizeof_fmt(memory.vms))

        except ImportError:
            pass

        table.add('Greenlets', gevent.get_hub().loop.activecnt)
        event.msg.reply(table.compile())

    @Plugin.command('objects', group='debug', level=CommandLevels.TRUSTED)
    def debug_memory(self, event):
        by_count = Counter()
        by_size = Counter()

        for obj in gc.get_objects():
            size = sys.getsizeof(obj)
            by_count[type(obj).__name__] += 1
            by_size[type(obj).__name__] += size

        codeblock = '```python\n{}\n```'
        event.msg.reply('\nBy Count: {}\n By Size: {}\n'.format(
            codeblock.format('\n'.join('{}: {}'.format(k, v) for k, v in by_count.most_common(15))),
            codeblock.format('\n'.join('{}: {}'.format(k, v) for k, v in by_size.most_common(15))),
        ))

    @Plugin.command('clean', '[size:int] [mode:str]', level=CommandLevels.TRUSTED)
    def clean(self, event, size=10, mode=None):
        if len(self.state.messages[event.channel.id]) < self.state.messages[event.channel.id].maxlen:
            self.state.fill_messages(event.channel)

        if mode in ('any', 'all'):
            msgs = take(reversed(self.state.messages[event.channel.id]), size)
        else:
            msgs = take(reversed([
                i for i in self.state.messages[event.channel.id]
                if i.author_id == self.state.me.id]), size)

        msgs = list(msgs)
        msg = event.msg.reply('Deleting {} messages...'.format(len(msgs)))
        event.channel.delete_messages(msgs)
        gevent.sleep(5)
        msg.delete()

    @Plugin.command('plugins')
    def plugins(self, event):
        event.msg.reply('`{}`'.format(', '.join(self.bot.plugins.keys())))

    @Plugin.command('reload', '<plugin:str>', level=CommandLevels.OWNER, oob=True)
    def on_reload(self, event, plugin):
        def reload_call(bot):
            bot.plugins[plugin].reload()

        if self.bot.shards:
            self.bot.shards.all(reload_call)
        else:
            reload_call(self.bot)
        event.msg.reply('Reloaded {}'.format(plugin))

    @Plugin.command('block', '<entity:user>', level=CommandLevels.OWNER)
    def block(self, event, entity):
        """
        Blocks the given user/role from the current channel.
        """
        event.channel.create_overwrite(entity, deny=Permissions.READ_MESSAGES)
        event.msg.reply(u'Blocked {} from viewing this channel'.format(entity))

    @Plugin.command('eval', level=CommandLevels.TRUSTED)
    def command_eval(self, event):
        ctx = {
            'bot': self.bot,
            'client': self.bot.client,
            'state': self.bot.client.state,
            'event': event,
            'msg': event.msg,
            'guild': event.msg.guild,
            'channel': event.msg.channel,
            'author': event.msg.author
        }

        # Mulitline eval
        src = event.codeblock
        if src.count('\n'):
            lines = filter(bool, src.split('\n'))
            if lines[-1] and 'return' not in lines[-1]:
                lines[-1] = 'return ' + lines[-1]
            lines = '\n'.join('    ' + i for i in lines)
            code = 'def f():\n{}\nx = f()'.format(lines)
            local = {}

            try:
                exec(compile(code, '<eval>', 'exec'), ctx, local)
            except Exception as e:
                event.msg.reply(PY_CODE_BLOCK.format(type(e).__name__ + ': ' + str(e)))
                return

            event.msg.reply(PY_CODE_BLOCK.format(pprint.pformat(local['x'])))
        else:
            try:
                result = eval(src, ctx)
            except Exception as e:
                event.msg.reply(PY_CODE_BLOCK.format(type(e).__name__ + ': ' + str(e)))
                return

            event.msg.reply(PY_CODE_BLOCK.format(result))
