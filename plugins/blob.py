import os
import re
import base64
import requests

from peewee import SqliteDatabase, Model, TextField, BigIntegerField, IntegerField
from holster.enum import Enum
from StringIO import StringIO
from PIL import Image
from gevent.lock import Semaphore

from disco.bot import Plugin, Config, CommandLevels
from disco.gateway.events import MessageReactionAdd


db = SqliteDatabase('emojis.db')

# TODO
#  - Make sure the state transitions are more concrete
#  - Add prune command (deletes stale/bad emojis)
#  - Add reload command or smth that reloads all reactions


class Submission(Model):
    State = Enum(
        'COUNCIL_QUEUE',
        'APPROVAL_QUEUE',
        'DENIED',
        'APPROVED',
    )

    class Meta:
        database = db

    name = TextField()
    author = BigIntegerField()
    contents = TextField(null=True)

    temp_emoji_id = BigIntegerField(null=True)
    submission_queue_msg = BigIntegerField(null=True)
    council_queue_msg = BigIntegerField(null=True)
    approval_queue_msg = BigIntegerField(null=True)

    yay = IntegerField(default=0)
    nay = IntegerField(default=0)

    state = IntegerField(default=State.COUNCIL_QUEUE)


EMOJI_NAME_RE = re.compile(r':([a-zA-Z0-9_]+):')
BAD_SUGGESTION_MSG = 'Heya! Looks like you tried to suggest\
 an emoji to the Google Blob Server. Unfortunately it looks like\
 you didnt send your message in the right format, so I wasn\'t able\
 to understand it. To suggest an emoji, you must post the emoji name,\
 like so: `:my_emoji_name:` and upload the emoji as an attachment. Feel\
 free to try again, and if you are still having problems ping a moderator!'

SUGGESTION_RECIEVED = 'Thanks for your emoji submission to the\
 Google Blob Server! It\'s been added to our internal vote queue,\
 so expect an update soon!'


GREEN_TICK_ID = 305231298799206401
RED_TICK_ID = 305231335512080385

GREEN_TICK_EMOJI = 'green_tick:305231298799206401'
RED_TICK_EMOJI = 'red_tick:305231335512080385'


class BlobPluginConfig(Config):
    # LIVE
    suggestion_channel = 295012914564169728
    council_queue_channel = 294924110130184193
    council_changelog_channel = 298920394751082507
    approval_queue_channel = 289847856033169409
    emoji_role = 292388383823495168

    # TESTING
    # suggestion_channel = 305229423953838080
    # council_queue_channel = 305229442769223680
    # council_changelog_channel = 305548352530153472
    # approval_queue_channel = 305547010541617167
    # emoji_role = 305523085715570688


@Plugin.with_config(BlobPluginConfig)
class BlobPlugin(Plugin):
    def load(self, ctx):
        super(BlobPlugin, self).load(ctx)

        self.update_lock = Semaphore()

        Submission.create_table(True)
        if not os.path.exists('emojis'):
            os.mkdir('emojis')

    @property
    def suggestion(self):
        return self.state.channels.get(self.config.suggestion_channel)

    @property
    def council_queue(self):
        return self.state.channels.get(self.config.council_queue_channel)

    @property
    def council_changelog(self):
        return self.state.channels.get(self.config.council_changelog_channel)

    @property
    def approval_queue(self):
        return self.state.channels.get(self.config.approval_queue_channel)

    @Plugin.listen('MessageDelete')
    def on_message_delete(self, event):
        try:
            sub = Submission.select().where(
                ((Submission.council_queue_msg == event.id) |
                (Submission.approval_queue_msg == event.id)) &
                (Submission.state != Submission.State.DENIED) &
                (Submission.state != Submission.State.APPROVED)
            ).get()
        except Submission.DoesNotExist:
            return

        self.log.info('Message was deleted for submission %s, marking submission as denied', sub.id)
        self.council_deny(event, sub)

    @Plugin.listen('MessageCreate')
    def on_message_create(self, event):
        if event.author.id == self.client.state.me.id:
            return

        if event.channel.id != self.config.suggestion_channel:
            return

        # Make sure that the suggestion has a name and a single attachment
        name = EMOJI_NAME_RE.findall(event.content)
        if not name or not len(event.attachments) == 1:
            event.delete()
            event.author.chain().open_dm().send_message(BAD_SUGGESTION_MSG)
            return
        name = name[0]

        # Download, resize and post the emoji
        url = list(event.attachments.values())[0].url
        r = requests.get(url)

        try:
            r.raise_for_status()
        except:
            event.delete()
            event.author.chain().open_dm().send_message(BAD_SUGGESTION_MSG)
            self.log.exception('Failed to download uploaded attachment: ')
            return

        sub = Submission.create(
            name=name,
            author=event.author.id,
        )

        # Save the emoji on disk
        img = Image.open(StringIO(r.content))
        with open('emojis/{}.png'.format(sub.id), 'w') as f:
            img.save(f)

        img_raw = StringIO()
        img.save(img_raw, 'PNG')
        img_raw.seek(0, 0)

        try:
            # Upload the emoji temporarily
            emoji = self.client.api.guilds_emojis_create(
                event.guild.id,
                name='{}_{}'.format(name, sub.id),
                roles=[self.config.emoji_role],
                image='data:image/png;base64,' + base64.b64encode(img_raw.getvalue()))
        except Exception:
            event.delete()
            event.author.chain().open_dm().send_message(BAD_SUGGESTION_MSG)
            return

        # Post submission to council queue
        msg_contents = u'{} (`{}`) submitted by {} (`#{}`)'.format(
            emoji,
            name,
            event.author,
            sub.id,
        )

        cmsg = self.council_queue.send_message(msg_contents)\
            .chain(True)\
            .add_reaction(GREEN_TICK_EMOJI)\
            .add_reaction(RED_TICK_EMOJI)\
            .first()

        # Repost submission to suggestions channel
        msg_contents += ' [<{}>]'.format(url)
        smsg = self.suggestion.send_message(msg_contents)

        # Tell the user their submission was recieved
        event.author.chain().open_dm().send_message(SUGGESTION_RECIEVED)

        # Delete the submission message
        event.delete()

        sub.council_queue_msg = cmsg.id
        sub.submission_queue_msg = smsg.id
        sub.temp_emoji_id = emoji.id
        sub.contents = msg_contents
        sub.save()

    @Plugin.listen('MessageReactionAdd', 'MessageReactionRemove')
    def on_message_reaction_add(self, event):
        update = None

        if event.user_id == self.client.state.me.id:
            return

        if event.channel_id != self.config.council_queue_channel:
            return

        unit = 1 if isinstance(event, MessageReactionAdd) else - 1

        if event.emoji.id == GREEN_TICK_ID:
            update = {'yay': Submission.yay + unit}
        elif event.emoji.id == RED_TICK_ID:
            update = {'nay': Submission.nay + unit}
        else:
            return

        with self.update_lock:
            Submission.update(**update).where(
                (
                    (Submission.state == Submission.State.COUNCIL_QUEUE) &
                    (Submission.council_queue_msg == event.message_id)
                )
            ).execute()

            try:
                sub = Submission.select().where(
                    (
                        (Submission.state == Submission.State.COUNCIL_QUEUE) &
                        (Submission.council_queue_msg == event.message_id)
                    )
                ).get()
            except Submission.DoesNotExist:
                return

            self.log.info('checking submission (%s: %s yay %s nay)', sub.id, sub.yay, sub.nay)
            if sub.yay >= 10 and sub.yay - sub.nay >= 5 and sub.yay + sub.nay >= 15:
                self.council_approve(sub)
            elif sub.nay >= 10 and sub.nay - sub.yay >= 5 and sub.yay + sub.nay >= 15:
                self.council_deny(event, sub, True)

    def council_approve(self, sub):
        self.log.info('Moving submission %s to approved', sub.id)
        sub.state = Submission.State.APPROVAL_QUEUE

        # Post to approval queue
        msg = self.approval_queue.send_message('<:{}:{}>'.format(sub.name, sub.temp_emoji_id))\
            .chain(True)\
            .add_reaction(GREEN_TICK_EMOJI)\
            .add_reaction(RED_TICK_EMOJI)\
            .first()
        sub.approval_queue_msg = msg.id

        # Post to changelog
        self.council_changelog.send_message('<:{}> moved to <#{}>: <:{}:{}> (by <@{}>)'.format(
            GREEN_TICK_EMOJI,
            self.config.approval_queue_channel,
            sub.name,
            sub.temp_emoji_id,
            sub.author,
        ))

        self.council_cleanup(sub)

    def council_deny(self, event, sub, changelog=False):
        self.log.info('Moving submission %s to denied', sub.id)
        sub.state = Submission.State.DENIED

        # Post to changelog
        if changelog:
            self.council_changelog.send_message('<:{}> denied: <:{}:{}>'.format(
                RED_TICK_EMOJI,
                sub.name,
                sub.temp_emoji_id,
            ))

        event.guild.emojis.get(sub.temp_emoji_id).delete()
        self.council_cleanup(sub)

    def council_cleanup(self, sub):
        # Delete old messages
        council_queue_msg = sub.council_queue_msg
        submission_queue_msg = sub.submission_queue_msg
        sub.council_queue_msg = None
        sub.submission_queue_msg = None
        self.log.info('Cleaning up submission %s; got to save', sub.id)
        sub.save()

        # Must happen after save
        if council_queue_msg:
            self.council_queue.delete_message(council_queue_msg)

        if submission_queue_msg:
            self.suggestion.delete_message(submission_queue_msg)

    @Plugin.command('info', '<sid:int>', group='blob', level=CommandLevels.TRUSTED)
    def on_blob_info(self, event, sid):
        try:
            sub = Submission.get(id=sid)
        except Submission.DoesNotExist:
            return event.msg.reply('Invalid submission ID')

        # TODO: attachments

        event.msg.reply('[{}] `{}` submitted by {} (state = {}, yay = {}, nay = {})'.format(
            sub.id,
            sub.name,
            self.state.users.get(sub.author),
            sub.state,
            sub.yay,
            sub.nay
        ))

    @Plugin.command('deny', '<sid:int>', group='blob', level=CommandLevels.TRUSTED)
    def on_blob_deny(self, event, sid):
        try:
            sub = Submission.get(id=sid)
        except Submission.DoesNotExist:
            return event.msg.reply('Invalid submission ID')

        if sub.state == Submission.State.COUNCIL_QUEUE.index:
            self.council_deny(event, sub, True)
            return event.msg.reply(':ok_hand: denied')

        if sub.state != Submission.State.APPROVAL_QUEUE.index:
            return event.msg.reply('Not in approval queue')

        self.approval_queue.delete_message(sub.approval_queue_msg)
        sub.approval_queue_msg = None
        event.guild.emojis.get(sub.temp_emoji_id).delete()
        event.msg.reply(':ok_hand: denied that emoji')
        sub.state = Submission.State.DENIED
        sub.save()

    @Plugin.command('approve', '<sid:int> [name:str]', group='blob', level=CommandLevels.TRUSTED)
    def on_blob_approve(self, event, sid, name=None):
        try:
            sub = Submission.get(id=sid)
        except Submission.DoesNotExist:
            return event.msg.reply('Invalid submission ID')

        if sub.state == Submission.State.COUNCIL_QUEUE.index:
            self.council_approve(sub)
            return event.msg.reply(':ok_hand: sent to approval queue')

        if sub.state != Submission.State.APPROVAL_QUEUE.index:
            return event.msg.reply('Not in approval queue')

        if not name:
            return event.msg.reply('no name provided, wolfiri plz')

        self.approval_queue.delete_message(sub.approval_queue_msg)
        sub.approval_queue_msg = None
        event.guild.emojis.get(sub.temp_emoji_id).update(roles=[], name=name)
        event.msg.reply(':ok_hand: approved that emoji')
        sub.state = Submission.State.APPROVED
        sub.save()
