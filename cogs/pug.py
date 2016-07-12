import asyncio
import arrow
from discord.ext import commands
import discord
import random
import shelve
import os

PICKMODES = [
        [0, 1, 0, 1, 0, 1, 0, 1],
        [0, 1, 1, 0, 0, 1, 1, 0]]


class Mod:
    """Maintains the players for a PUG"""

    def __init__(self, name, players, teams, pickmode):
        self.name = name
        self.max_players = players
        self.players = []
        if teams:
            self.teams = [ [], [] ]
            self.pickmode = pickmode

    def __len__(self):
        return len(self.players)

    def __str__(self):
        s = self.name + ' [{}/{}] '.format(len(self), self.max_players)
        for i, p in enumerate(self.players):
            if p is None: continue
            s += '{}) {} '.format(str(i + 1), p.name)
        return s

    @property
    def isfull(self):
        return len(self) == self.max_players

    @property
    def isteamgame(self):
        return hasattr(self, 'teams')

    @property
    def red_team(self):
        return self.teams[0] if self.isteamgame else []

    @property
    def blue_team(self):
        return self.teams[1] if self.isteamgame else []

    @property
    def hascaptains(self):
        return self.red_team and self.blue_team

    @property
    def current_team(self):
        index = len(self.red_team) + len(self.blue_team) - 2
        return PICKMODES[self.pickmode][index]

    @property
    def current_captain(self):
        return self.teams[self.current_team][0]

    @property
    def teamsready(self):
        return len(self.red_team) + len(self.blue_team) == self.max_players

    def add_player(self, player):
        if player in self.players or self.isfull:
            return False
        self.players.append(player)
        return True

    def del_player(self, player):
        if player in self.players + self.red_team + self.blue_team:
            self.reset()
            self.players.remove(player)
            return True
        return False

    def set_captain(self, player):
        if player in self.players and self.isfull:
            i = self.players.index(player)
            if len(self.red_team) == 0:
                self.red_team.append(player)
            elif len(self.blue_team) == 0:
                self.blue_team.append(player)
            else:
                return False
            self.players[i] = None
            return True
        return False

    def ispicking(self, captain, team):
        return self.teams[team] and self.teams[team][0] == captain

    def pick_player(self, captain, index):
        if not (self.isteamgame and self.isfull and self.hascaptains):
            return False
        team = self.current_team
        if self.ispicking(captain, team):
            if index < 0 or index >= len(self) or self.players[index] is None:
                return False
            player = self.players[index]
            self.teams[team].append(player)
            self.players[index] = None
            self.check_final_picks()
            return True

    def check_final_picks(self):
        index = len(self.red_team) + len(self.blue_team) - 2
        remaining = PICKMODES[self.pickmode][index:self.max_players-2]
        if len(set(remaining)) == 1:
            team = remaining[0]
            for p in self.players:
                if p is not None:
                    self.teams[team].append(p)

    def reset(self):
        if self.isteamgame:
            self.players = self.red_team + self.blue_team + self.players
            self.players = [p for p in self.players if p is not None]
            self.teams = [ [], [] ]

    def full_reset(self):
        self.players = []
        if self.isteamgame:
            self.teams = [ [], [] ]


class PUG:
    """PUG related commands"""

    def __init__(self, bot):
        self.bot = bot
        with shelve.open('data/pug') as db:
            self.channels = db.get('channels', dict())
            for channel in self.channels:
                self.channels[channel].full_reset()

    @commands.command(pass_context=True, no_pm=True)
    @commands.has_permissions(manage_channels=True)
    async def addmod(self, context, mod: str, n: int, teams: bool = True):
        """Sets the mod for the channel"""
        if n == 2: teams = False
        if n < 2 or n > 10 or (teams and (n < 4 or n % 2 == 1)):
            return
        pickmode = 0 if n == 4 else 1
        channel = context.message.channel
        self.channels[channel] = Mod(mod, n, teams, pickmode)
        with shelve.open('data/pug') as db:
            db['channels'] = self.channels
            stats = db.setdefault('stats', dict())
            stats[channel] = dict()
            db['stats'] = stats

    @commands.command(pass_context=True, no_pm=True)
    @commands.has_permissions(manage_channels=True)
    async def delmod(self, context):
        """Deletes the mod for the channel"""
        channel = context.message.channel
        if channel not in self.channels:
            return
        del self.channels[channel]
        with shelve.open('data/pug') as db:
            db['channels'] = self.channels
            stats = db.get('stats', dict())
            if channel in stats:
                del stats[channel]
            db['stats'] = stats

    @commands.command(pass_context=True, no_pm=True, aliases=['ls'])
    async def list(self, context):
        """Display players in the PUG"""
        mod = self.channels.get(context.message.channel)
        if mod is not None:
            await self.bot.say(str(mod))

    async def add_player(self, channel, player):
        if player.bot: return
        mod = self.channels.get(channel)
        if mod is None or not mod.add_player(player):
            return
        await self.bot.send_message(player, 'You have joined ' + mod.name)
        if mod.isfull:
            s = ' '.join([p.mention for p in mod.players])
            s += '\n{} has been filled'.format(mod.name)
            await self.bot.say(s)
            if mod.isteamgame:
                await asyncio.sleep(10)
                await self.rand_captains(channel)
            else:
                self.save_dm_stats(channel, mod)
                mod.full_reset()

    @commands.command(pass_context=True, no_pm=True)
    @commands.has_permissions(manage_channels=True)
    async def addplayer(self, context, player: discord.Member):
        """Adds player to PUG"""
        await self.add_player(context.message.channel, player)

    @commands.command(pass_context=True, no_pm=True, aliases=['j'])
    async def join(self, context):
        """Joins PUG"""
        await self.add_player(context.message.channel, context.message.author)

    @commands.command(pass_context=True, no_pm=True)
    async def promote(self, context):
        mod = self.channels.get(context.message.channel)
        if mod is None: return
        n = mod.max_players - len(mod)
        await self.bot.say('@here Only {} more needed for {}'.format(n, mod.name))

    async def del_player(self, channel, player):
        mod = self.channels.get(channel)
        if mod is None: return
        wasfull = mod.isfull
        if mod.del_player(player):
            if wasfull:
                mod.reset()
                await self.bot.say('{} has been reset'.format(mod.name))

    @commands.command(pass_context=True, no_pm=True)
    @commands.has_permissions(manage_channels=True)
    async def delplayer(self, context, player: discord.User):
        """Removes player from PUG"""
        await self.del_player(context.message.channel, player)

    @commands.command(pass_context=True, no_pm=True, aliases=['l'])
    async def leave(self, context):
        """Leave PUG"""
        await self.del_player(context.message.channel, context.message.author)

    async def on_member_update(self, before, after):
        """Remove players if they go offline"""
        if before.status == discord.Status.online:
            if after.status == discord.Status.offline:
                for channel in self.channels:
                    await self.del_player(channel, before)

    async def on_channel_delete(self, channel):
        """
        When a channel is deleted remove the PUG channel
        and remove the assoicated stats
        """
        if channel in self.channels:
            del self.channels[channel]
            with shelve.open('data/pug') as db:
                db['channels'] = self.channels
                stats = db.get('stats', dict())
                if channel in stats:
                    del stats[channel]
                db['stats'] = stats

    @commands.command(pass_context=True, no_pm=True)
    @commands.has_permissions(manage_channels=True)
    async def reset(self, context):
        channel = context.message.channel
        mod = self.channels.get(channel)
        if mod is not None and mod.isfull and mod.isteamgame:
            await self.bot.say('{} has been reset'.format(mod.name))
            mod.reset()
            await asyncio.sleep(10)
            await self.rand_captains(channel)

    @commands.command(pass_context=True, no_pm=True)
    @commands.has_permissions(manage_channels=True)
    async def fullreset(self, context):
        mod = self.channels.get(context.message.channel)
        if mod is not None:
            mod.full_reset()

    async def rand_captains(self, channel):
        mod = self.channels.get(channel)
        if mod is None: return
        if mod.isfull and mod.isteamgame and not mod.hascaptains:
            candidates = [i for i, x in enumerate(mod.players) if x is not None]
            random.shuffle(candidates)
            msg = ''
            if len(candidates) == len(mod.players):
                mod.set_captain(mod.players[candidates[0]])
                msg += mod.red_team[0].name + ' is captain for Red Team\n'
            mod.set_captain(mod.players[candidates[1]])
            msg += mod.blue_team[0].name + ' is captain for Blue Team\n'
            msg += '{} to pick'.format(mod.current_captain.mention)
            await self.bot.say(msg)

    async def set_captain(self, channel, player):
        mod = self.channels.get(channel)
        if mod is not None and mod.set_captain(player):
            if mod.hascaptains:
                captain = mod.current_captain
                await self.bot.say('{} to pick'.format(captain.mention))

    @commands.command(pass_context=True, no_pm=True)
    @commands.has_permissions(manage_channels=True)
    async def setcaptain(self, context, player: discord.User):
        await self.set_captain(context.message.channel, player)

    @commands.command(pass_context=True, no_pm=True)
    async def captain(self, context):
        await self.set_captain(context.message.channel, context.message.author)

    @commands.command(pass_context=True, no_pm=True)
    async def here(self, context):
        """Prevent being kicked when set as captain"""
        pass

    @commands.command(pass_context=True, no_pm=True, aliases=['p'])
    async def pick(self, context, player: int):
        """Pick player by index"""
        mod = self.channels.get(context.message.channel)
        if mod is None: return
        mod.pick_player(context.message.author, player - 1)
        if mod.teamsready:
            s = 'Red Team: ' + ' '.join([p.mention for p in mod.red_team])
            s += '\nBlue Team: ' + ' '.join([p.mention for p in mod.blue_team])
            await self.bot.say(s)
            self.save_team_stats(context.message.channel, mod)
            mod.full_reset()

    @commands.command(pass_context=True, no_pm=True)
    async def teams(self, context):
        """Display current teams"""
        mod = self.channels.get(context.message.channel)
        if mod is None or not (mod.isteamgame and mod.isfull):
            return
        s = 'Red Team: ' + ' '.join([p.name for p in mod.red_team])
        s += '\nBlue Team: ' + ' '.join([p.name for p in mod.blue_team])
        await self.bot.say(s)

    @commands.command(pass_context=True, no_pm=True)
    async def turn(self, context):
        """Display captain whose turn it is to pick"""
        mod = self.channels.get(context.message.channel)
        if mod is None or not (mod.isteamgame and mod.isfull):
            return
        captain = mod.current_captain
        await self.bot.say(captain.name + ' to pick')

    def save_dm_stats(self, channel, mod):
        with shelve.open('data/pug') as db:
            stats = db.get('stats', dict())
            channel_stats = stats.setdefault(channel, dict())
            for p in mod.players:
                pstats = channel_stats.setdefault(p, dict(total=0))
                pstats['total'] += 1
                pstats['last'] = arrow.utcnow()
            pstats = channel_stats.setdefault(self.bot.user, dict(total=0))
            pstats['total'] += 1
            pstats['last'] = arrow.utcnow()
            pstats['players'] = ' '.join([p.name for p in mod.players])
            db['stats'] = stats

    def get_picks(self, mod, team):
        xs = PICKMODES[mod.pickmode][:mod.max_players-2]
        picks = [0] + [i+1 for i, x in enumerate(xs) if x == team]
        return picks

    def get_pickorder(self, mod):
        return self.get_picks(mod, 0) + self.get_picks(mod, 1)

    def save_team_stats(self, channel, mod):
        with shelve.open('data/pug') as db:
            stats = db.get('stats', dict())
            channel_stats = stats.setdefault(channel, dict())
            pickorder = self.get_pickorder(mod)
            players = mod.red_team + mod.blue_team
            for i in range(mod.max_players):
                p = players[i]
                pstats = channel_stats.setdefault(p, dict(total=0,captain=0,totalpicks=0))
                pstats['total'] += 1
                if pickorder[i] == 0: pstats['captain'] += 1
                else: pstats['totalpicks'] += pickorder[i]
                pstats['last'] = arrow.utcnow()
                channel_stats[p] = pstats
            pugstats = channel_stats.setdefault(self.bot.user, dict(total=0))
            pugstats['total'] += 1
            pugstats['last'] = arrow.utcnow()
            players = 'Red Team: ' + ' '.join([p.name for p in mod.red_team])
            players += ' Blue Team: ' + ' '.join([p.name for p in mod.blue_team])
            pugstats['players'] = players
            stats[channel] = channel_stats
            db['stats'] = stats

    def get_stats(self, stats, mod):
        total = stats.get('total', 0)
        s = 'Total [{}]'.format(stats.get('total', 0))
        captain = stats.get('captain')
        if captain is not None:
            s += ' Captain [{}]'.format(captain)
            games = total - captain
            totalpick = stats.get('totalpicks', 0)
            averagepick = 0 if games == 0 else totalpick / games
            s += ' Avg. [{:.2f}/{}]'.format(averagepick, mod.max_players - 2)
        s += ' Last [{}]'.format(arrow.get(stats['last']).humanize())
        if 'players' in stats:
            s += ' ' + stats['players']
        return s

    async def display_stats(self, channel, player):
        mod = self.channels.get(channel)
        if mod is None: return
        with shelve.open('data/pug') as db:
            stats = db.get('stats', dict())
            channel_stats = stats.get(channel, dict())
            pstats = channel_stats.get(player)
            if pstats is not None:
                s = self.get_stats(pstats, mod)
                await self.bot.say(s)


    @commands.command(pass_context=True, no_pm=True)
    async def stats(self, context, player: discord.Member):
        """Display PUG stats for player"""
        await self.display_stats(context.message.channel, player)

    @commands.command(pass_context=True, no_pm=True)
    async def mystats(self, context):
        """Display your PUG stats"""
        await self.display_stats(context.message.channel, context.message.author)

    @commands.command(pass_context=True, no_pm=True)
    async def pugstats(self, context):
        """Display channel PUG stats"""
        await self.display_stats(context.message.channel, self.bot.user)

    @commands.command(pass_context=True, no_pm=True)
    async def last(self, context):
        """Display last PUG"""
        pass

    @commands.command(pass_context=True, no_pm=True)
    async def liast(self, context):
        """Display last PUG and current players"""
        pass


def setup(bot):
    if not os.path.exists('data'):
        os.makedirs('data')
    bot.add_cog(PUG(bot))
