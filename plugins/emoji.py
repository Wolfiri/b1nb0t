import re
import base64
import requests

from disco.bot import Plugin, CommandLevels


EMOJI_RE = re.compile(r'<:.+:([0-9]+)>')


class EmojiPlugin(Plugin):
    @Plugin.command('info', group='emoji', level=CommandLevels.MOD)
    def emoji_info(self, event):
        return event.msg.reply(
            ':information_source: global custom emojis because b1nzy is a cheater (limit: 200)')

    @Plugin.command('add', '<name:str> [url:str]', group='emoji', level=CommandLevels.MOD)
    def add_emoji(self, event, name, url=None):
        if len(list(event.guild.emojis.select(managed=True))) >= 200:
            return event.msg.reply(':warning: cannot add emojis, server has surpassed maximum')

        if not url:
            if not len(event.msg.attachments):
                return event.msg.reply(':warning: pls upload an image or add a url')
            url = next(event.msg.attachments.values()).url

        data = requests.get(url).content

        emoji = self.client.api.guilds_emojis_create(
            event.guild.id,
            name=name,
            image='data:image/png;base64,' + base64.b64encode(data))
        event.msg.reply(':ok_hand: added your emoji: {}'.format(str(emoji)))

    @Plugin.command('rmv', '<name:str>', group='emoji', level=CommandLevels.MOD)
    def rmv_emoji(self, event, name):
        match = EMOJI_RE.match(name)
        if not match:
            obj = event.guild.emojis.select_one(name=name)
            if not obj:
                return event.msg.reply(':warning: invalid emoji `{}`'.format(name))
            eid = obj.id
        else:
            eid = match.group(1)

        self.client.api.guilds_emojis_delete(
            event.guild.id,
            eid)

        event.msg.reply(':ok_hand: removed your emoji')

    @Plugin.command('rename', '<emoji:str> <name:str>', group='emoji', level=CommandLevels.MOD)
    def rename_emoji(self, event, emoji, name):
        emoji_obj = None

        match = EMOJI_RE.match(emoji)
        if match:
            emoji_obj = event.guild.emojis.get(int(match.group(1)))
        else:
            emoji_obj = event.guild.emojis.select_one(name=name)

        if not emoji_obj:
            return event.msg.reply(':warning: invalid emoji')

        emoji_obj.update(name=name)
        return event.msg.reply(u':ok_hand: renamed emoji to {}'.format(name))

    @Plugin.command('list', group='emoji', level=CommandLevels.MOD)
    def list_emoji(self, event):
        emojis = list(event.guild.emojis.select(managed=True))
        if not len(emojis):
            return event.msg.reply(':warning: no custom emoji')

        event.msg.reply('{}'.format(
            '\n'.join(
                '{}: {}'.format(i.id, i.name) for i in emojis
            )
        ))

    @Plugin.command('url', '<name:str>', group='emoji', level=CommandLevels.MOD)
    def url_emoji(self, event, name):
        obj = EMOJI_RE.findall(name)
        if not len(obj):
            return event.msg.reply('Invalid emoji')

        return event.msg.reply('<https://discordapp.com/api/emojis/{}.png>'.format(obj[0]))
