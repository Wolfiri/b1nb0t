import socket
import subprocess

from ipwhois import IPWhois

from disco.bot import Plugin, CommandLevels
from disco.types.message import MessageEmbed


class NetworkPlugin(Plugin):
    def load(self, ctx):
        super(NetworkPlugin, self).load(ctx)

    @Plugin.command('ping', '<host:str> [count:int]', level=CommandLevels.TRUSTED)
    def ping(self, event, host, count=5):
        msg = event.msg.reply('Pinging {}...'.format(host))
        proc = subprocess.Popen(['ping', '-c{}'.format(count), '-w10', host], stdout=subprocess.PIPE)
        msg.edit(u'```{}```'.format(proc.stdout.read()))

    @Plugin.command('mtr', '<host:str> [count:int]', level=CommandLevels.TRUSTED)
    def mtr(self, event, host, count=5):
        msg = event.msg.reply('Running mtr on {}...'.format(host))
        proc = subprocess.Popen([
            'mtr',
            '--timeout=10',
            '--report',
            '--report-cycles={}'.format(count),
            host
        ], stdout=subprocess.PIPE)
        msg.edit(u'```{}```'.format(proc.stdout.read()))

    @Plugin.command('ipinfo', '<host:str>', level=CommandLevels.TRUSTED)
    def whois(self, event, host):
        host = socket.gethostbyname(host)
        data = IPWhois(address=host).lookup_rdap()

        embed = MessageEmbed()
        embed.add_field(name='ASN', value=data['asn'], inline=True)
        embed.add_field(name='CIDR', value=data['asn_cidr'], inline=True)
        embed.add_field(name='Country', value=data['asn_country_code'], inline=True)
        embed.add_field(name='Network Name', value=data['network']['name'], inline=True)
        event.msg.reply('', embed=embed)
