"""
utils.py - PyLink utilities module.

This module contains various utility functions related to IRC and/or the PyLink
framework.
"""

import string
import re
import importlib
import os
import collections

from log import log
import world
import conf

class NotAuthenticatedError(Exception):
    """
    Exception raised by checkAuthenticated() when a user fails authentication
    requirements.
    """
    pass

class IncrementalUIDGenerator():
    """
    Incremental UID Generator module, adapted from InspIRCd source:
    https://github.com/inspircd/inspircd/blob/f449c6b296ab/src/server.cpp#L85-L156
    """

    def __init__(self, sid):
        if not (hasattr(self, 'allowedchars') and hasattr(self, 'length')):
             raise RuntimeError("Allowed characters list not defined. Subclass "
                                "%s by defining self.allowedchars and self.length "
                                "and then calling super().__init__()." % self.__class__.__name__)
        self.uidchars = [self.allowedchars[0]]*self.length
        self.sid = sid

    def increment(self, pos=None):
        """
        Increments the UID generator to the next available UID.
        """
        # Position starts at 1 less than the UID length.
        if pos is None:
            pos = self.length - 1

        # If we're at the last character in the list of allowed ones, reset
        # and increment the next level above.
        if self.uidchars[pos] == self.allowedchars[-1]:
            self.uidchars[pos] = self.allowedchars[0]
            self.increment(pos-1)
        else:
            # Find what position in the allowed characters list we're currently
            # on, and add one.
            idx = self.allowedchars.find(self.uidchars[pos])
            self.uidchars[pos] = self.allowedchars[idx+1]

    def next_uid(self):
        """
        Returns the next unused UID for the server.
        """
        uid = self.sid + ''.join(self.uidchars)
        self.increment()
        return uid

def add_cmd(func, name=None):
    """Binds an IRC command function to the given command name."""
    world.services['pylink'].add_cmd(func, name=name)
    return func

def add_hook(func, command):
    """Binds a hook function to the given command name."""
    command = command.upper()
    world.hooks[command].append(func)
    return func

_nickregex = r'^[A-Za-z\|\\_\[\]\{\}\^\`][A-Z0-9a-z\-\|\\_\[\]\{\}\^\`]*$'
def isNick(s, nicklen=None):
    """Returns whether the string given is a valid nick."""
    if nicklen and len(s) > nicklen:
        return False
    return bool(re.match(_nickregex, s))

def isChannel(s):
    """Returns whether the string given is a valid channel name."""
    return str(s).startswith('#')

def _isASCII(s):
    """Returns whether the string given is valid ASCII."""
    chars = string.ascii_letters + string.digits + string.punctuation
    return all(char in chars for char in s)

def isServerName(s):
    """Returns whether the string given is a valid IRC server name."""
    return _isASCII(s) and '.' in s and not s.startswith('.')

hostmaskRe = re.compile(r'^\S+!\S+@\S+$')
def isHostmask(text):
    """Returns whether the given text is a valid hostmask."""
    # Band-aid patch here to prevent bad bans set by Janus forwarding people into invalid channels.
    return hostmaskRe.match(text) and '#' not in text

def parseModes(irc, target, args):
    """Parses a modestring list into a list of (mode, argument) tuples.
    ['+mitl-o', '3', 'person'] => [('+m', None), ('+i', None), ('+t', None), ('+l', '3'), ('-o', 'person')]

    This method is deprecated. Use irc.parseModes() instead.
    """
    log.warning("(%s) utils.parseModes is deprecated. Use irc.parseModes() instead!", irc.name)
    return irc.parseModes(target, args)

def applyModes(irc, target, changedmodes):
    """Takes a list of parsed IRC modes, and applies them on the given target.

    The target can be either a channel or a user; this is handled automatically.

    This method is deprecated. Use irc.applyModes() instead.
    """
    log.warning("(%s) utils.applyModes is deprecated. Use irc.applyModes() instead!", irc.name)
    return irc.applyModes(target, changedmodes)

def loadModuleFromFolder(name, folder):
    """
    Imports and returns a module, if existing, from a specific folder.
    """
    fullpath = os.path.join(folder, '%s.py' % name)
    m = importlib.machinery.SourceFileLoader(name, fullpath).load_module()
    return m

def getProtocolModule(protoname):
    """
    Imports and returns the protocol module requested.
    """
    return loadModuleFromFolder(protoname, world.protocols_folder)

def getDatabaseName(dbname):
    """
    Returns a database filename with the given base DB name appropriate for the
    current PyLink instance.

    This returns '<dbname>.db' if the running config name is PyLink's default
    (config.yml), and '<dbname>-<config name>.db' for anything else. For example,
    if this is called from an instance running as './pylink testing.yml', it
    would return '<dbname>-testing.db'."""
    if conf.confname != 'pylink':
        dbname += '-%s' % conf.confname
    dbname += '.db'
    return dbname

class ServiceBot():
    def __init__(self, name, default_help=True, default_request=False, default_list=True,
                 nick=None, ident=None, manipulatable=False):
        # Service name
        self.name = name

        # Nick/ident to take. Defaults to the same as the service name if not given.
        self.nick = nick or name
        self.ident = ident or name

        # Tracks whether the bot should be manipulatable by the 'bots' plugin and other commands.
        self.manipulatable = manipulatable

        # We make the command definitions a dict of lists of functions. Multiple
        # plugins are actually allowed to bind to one function name; this just causes
        # them to be called in the order that they are bound.
        self.commands = collections.defaultdict(list)

        # This tracks the UIDs of the service bot on different networks, as they are
        # spawned.
        self.uids = {}

        if default_help:
            self.add_cmd(self.help)

        if default_request:
            self.add_cmd(self.request)
            self.add_cmd(self.remove)

        if default_list:
            self.add_cmd(self.listcommands, 'list')

    def spawn(self, irc=None):
        # Spawn the new service by calling the PYLINK_NEW_SERVICE hook,
        # which is handled by coreplugin.
        if irc is None:
            for irc in world.networkobjects.values():
                irc.callHooks([None, 'PYLINK_NEW_SERVICE', {'name': self.name}])
        else:
            raise NotImplementedError("Network specific plugins not supported yet.")

    def reply(self, irc, text, notice=True):
        """Replies to a message using the right service UID."""
        servuid = self.uids.get(irc.name)
        if not servuid:
            log.warning("(%s) Possible desync? UID for service %s doesn't exist!", irc.name, self.name)
            return

        irc.reply(text, notice=notice, source=servuid)

    def call_cmd(self, irc, source, text):
        """
        Calls a PyLink bot command. source is the caller's UID, and text is the
        full, unparsed text of the message.
        """
        irc.called_by = source

        cmd_args = text.strip().split(' ')
        cmd = cmd_args[0].lower()
        cmd_args = cmd_args[1:]
        if cmd not in self.commands:
            self.reply(irc, 'Error: Unknown command %r.' % cmd)
            log.info('(%s/%s) Received unknown command %r from %s', irc.name, self.name, cmd, irc.getHostmask(source))
            return

        log.info('(%s/%s) Calling command %r for %s', irc.name, self.name, cmd, irc.getHostmask(source))
        for func in self.commands[cmd]:
            try:
                func(irc, source, cmd_args)
            except NotAuthenticatedError:
                self.reply(irc, 'Error: You are not authorized to perform this operation.')
            except Exception as e:
                log.exception('Unhandled exception caught in command %r', cmd)
                self.reply(irc, 'Uncaught exception in command %r: %s: %s' % (cmd, type(e).__name__, str(e)))

    def add_cmd(self, func, name=None):
        """Binds an IRC command function to the given command name."""
        if name is None:
            name = func.__name__
        name = name.lower()

        self.commands[name].append(func)
        return func

    def help(self, irc, source, args):
        """<command>

        Gives help for <command>, if it is available."""
        try:
            command = args[0].lower()
        except IndexError:  # No argument given, just return 'list' output
            self.listcommands(irc, source, args)
            return
        if command not in self.commands:
            self.reply(irc, 'Error: Unknown command %r.' % command)
            return
        else:
            funcs = self.commands[command]
            if len(funcs) > 1:
                self.reply(irc, 'The following \x02%s\x02 plugins bind to the \x02%s\x02 command: %s'
                           % (len(funcs), command, ', '.join([func.__module__ for func in funcs])))
            for func in funcs:
                doc = func.__doc__
                mod = func.__module__
                if doc:
                    lines = doc.split('\n')
                    # Bold the first line, which usually just tells you what
                    # arguments the command takes.
                    lines[0] = '\x02%s %s\x02' % (command, lines[0])
                    for line in lines:
                        # Then, just output the rest of the docstring to IRC.
                        self.reply(irc, line.strip())
                else:
                    self.reply(irc, "Error: Command %r doesn't offer any help." % command)
                    return

    def request(self, irc, source, args):
        self.reply(irc, "Request command stub called.")

    def remove(self, irc, source, args):
        self.reply(irc, "Remove command stub called.")

    def listcommands(self, irc, source, args):
        """takes no arguments.

        Returns a list of available commands this service has to offer."""

        cmds = list(self.commands.keys())
        cmds.sort()
        self.reply(irc, 'Available commands include: %s' % ', '.join(cmds))
        self.reply(irc, 'To see help on a specific command, type \x02help <command>\x02.')

def registerService(name, *args, **kwargs):
    """Registers a service bot."""
    name = name.lower()
    if name in world.services:
        raise ValueError("Service name %s is already bound!" % name)

    world.services[name] = sbot = ServiceBot(name, *args, **kwargs)
    sbot.spawn()
    return sbot

def unregisterService(name):
    """Unregisters an existing service bot."""
    assert name in world.services, "Unknown service %s" % name
    sbot = world.services[name]
    for ircnet, uid in sbot.uids.items():
        world.networkobjects[ircnet].proto.quit(uid, "Service unloaded.")

    del world.services[name]
