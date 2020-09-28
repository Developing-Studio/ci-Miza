try:
    from common import *
except ModuleNotFoundError:
    import os
    os.chdir("..")
    from common import *


class Restart(Command):
    name = ["Shutdown", "Reload", "Unload", "Reboot"]
    min_level = nan
    description = "Restarts, reloads, or shuts down ⟨MIZA⟩, with an optional delay."
    _timeout_ = inf

    async def __call__(self, message, channel, guild, argv, name, **void):
        bot = self.bot
        client = bot.client
        await message.add_reaction("❗")
        if name == "reload":
            await channel.send(f"Reloading {argv.lower()}...")
            succ = await create_future(bot.reload, argv.upper(), priority=True)
            if succ:
                return f"Successfully reloaded {argv.lower()}."
            return f"Error reloading {argv.lower()}. Please see log for more info."
        if name == "unload":
            await channel.send(f"Unloading {argv.lower()}...")
            succ = await create_future(bot.unload, argv, priority=True)
            if succ:
                return f"Successfully unloaded {argv.lower()}."
            return f"Error unloading {argv.lower()}. Please see log for more info."
        if argv:
            # Restart announcements for when a time input is specified
            if "in" in argv:
                argv = argv[argv.rindex("in") + 2:]
            delay = await bot.eval_time(argv, user)
            await channel.send("*Preparing to " + name + " in " + sec2time(delay) + "...*")
            emb = discord.Embed(colour=discord.Colour(1))
            emb.set_author(name=str(bot.user), url=bot.website, icon_url=best_url(bot.user))
            emb.description = f"I will be {'shutting down' if name == 'shutdown' else 'restarting'} in {sec2time(delay)}, apologies for any inconvenience..."
            await bot.send_event("_announce_", embed=emb)
            if delay > 0:
                await asyncio.sleep(delay)
        elif name == "shutdown":
            await channel.send("Shutting down... :wave:")
        else:
            await channel.send("Restarting... :wave:")
        with discord.context_managers.Typing(channel):
            with suppress(AttributeError):
                PRINT.close()
            t = time.time()
            # Call _destroy_ bot event to indicate to all databases the imminent shutdown
            await bot.send_event("_destroy_")
            # Save any database that has not already been autosaved
            await create_future(bot.update, priority=True)
            # Disconnect as many voice clients as possible
            futs = deque()
            for guild in client.guilds:
                member = guild.get_member(client.user.id)
                if member:
                    voice = member.voice
                    if voice:
                        futs.append(create_task(member.move_to(None)))
            for fut in futs:
                with suppress():
                    await fut
        with suppress():
            await client.close()
        if name.casefold() == "shutdown":
            touch(bot.shutdown)
        else:
            touch(bot.restart)
        with tracebacksuppressor:
            retry(os.remove, "log.txt", attempts=8, delay=0.1)
        if time.time() - t < 1:
            await asyncio.sleep(1)
        bot.close()
        del client
        del bot
        sys.exit()


class Execute(Command):
    name = ["Exec", "Eval"]
    min_level = nan
    description = "Causes all messages by the bot owner in the current channel to be executed as python code on ⟨MIZA⟩."
    usage = "<type> <enable(?e)> <disable(?d)>"
    flags = "aed"
    # Different types of terminals for different purposes
    terminal_types = demap({
        "null": 0,
        "main": 1,
        "relay": 2,
        "virtual": 4,
        "log": 8,
    })

    def __call__(self, bot, flags, argv, message, channel, guild, **void):
        update = bot.database.exec.update
        if not argv:
            argv = 0
        try:
            num = int(argv)
        except (TypeError, ValueError):
            out = argv.casefold()
            num = self.terminal_types[out]
        else:
            out = self.terminal_types[num]
        if "e" in flags or "a" in flags:
            if num == 0:
                num = 4
            try:
                bot.data.exec[channel.id] |= num
            except KeyError:
                bot.data.exec[channel.id] = num
            update()
            # Test bitwise flags for enabled terminals
            out = ", ".join(self.terminal_types.get(1 << i) for i in bits(bot.data.exec[channel.id]))
            create_task(message.add_reaction("❗"))
            return css_md(f"{sqr_md(out)} terminal now enabled in {sqr_md(channel)}.")
        elif "d" in flags:
            with suppress(KeyError):
                if num == 0:
                    # Test bitwise flags for enabled terminals
                    out = ", ".join(self.terminal_types.get(1 << i) for i in bits(bot.data.exec.pop(channel.id)))
                else:
                    bot.data.exec[channel.id] &= -num - 1
                    if not bot.data.exec[channel.id]:
                        bot.data.exec.pop(channel.id)
            update()
            return css_md(f"Successfully removed {sqr_md(out)} terminal.")
        out = iter2str({k: ", ".join(self.terminal_types.get(1 << i) for i in bits(v) for k, v in bot.data.exec.items())})
        return ini_md(f"Terminals currently set to {sqr_md(out)}")


class UpdateExec(Database):
    name = "exec"
    no_delete = True
    virtuals = cdict()
    listeners = cdict()

    qmap = {
        "“": '"',
        "”": '"',
        "„": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
        "〝": '"',
        "〞": '"',
        "⸌": "'",
        "⸍": "'",
        "⸢": "'",
        "⸣": "'",
        "⸤": "'",
        "⸥": "'",
    }
    qtrans = "".maketrans(qmap)

    # Custom print function to send a message instead
    _print = lambda self, *args, sep=" ", end="\n", prefix="", channel=None, **void: self.bot.send_as_embeds(channel, "```\n" + str(sep).join((i if type(i) is str else str(i)) for i in args) + str(end) + str(prefix) + "```")
    def _input(self, *args, channel=None, **kwargs):
        self._print(*args, channel=channel, **kwargs)
        self.listeners.__setitem__(channel.id, None)
        t = utc()
        while self.listeners[channel.id] is None and utc() - t < 86400:
            time.sleep(0.2)
        return self.listeners.pop(channel.id, None)

    # Asynchronously evaluates Python code
    async def procFunc(self, proc, channel, bot, term=0):
        # Main terminal uses bot's global variables, virtual one uses a shallow copy per channel
        if term & 1:
            glob = bot._globals
        else:
            try:
                glob = self.virtuals[channel.id]
            except KeyError:
                glob = self.virtuals[channel.id] = dict(bot._globals)
                glob.update(dict(
                    print=lambda *args, **kwargs: self._print(*args, channel=channel, **kwargs),
                    input=lambda *args, **kwargs: self._input(*args, channel=channel, **kwargs),
                    channel=channel,
                    guild=channel.guild,
                ))
        if "\n" not in proc:
            if proc.startswith("await "):
                proc = proc[6:]
        # Run concurrently to avoid blocking bot itself
        # Attempt eval first, then exec
        code = None
        with suppress(SyntaxError):
            code = await create_future(compile, proc, "<terminal>", "eval", optimize=2, priority=True)
        if code is None:
            with suppress(SyntaxError):
                code = await create_future(compile, proc, "<terminal>", "exec", optimize=2, priority=True)
            if code is None:
                _ = glob.get("_")
                func = "async def _():\n\tlocals().update(globals())\n"
                func += "\n".join("\t" + line for line in proc.splitlines())
                func += "\n\tglobals().update(locals())"
                code2 = await create_future(compile, func, "<terminal>", "exec", optimize=2, priority=True)
                await create_future(eval, code2, glob, priority=True)
                output = await glob["_"]()
                glob["_"] = _
        if code is not None:
            output = await create_future(eval, code, glob, priority=True)
        # Output sent to "_" variable if used
        if output is not None:
            glob["_"] = output 
        return output

    async def sendDeleteID(self, c_id, delete_after=20, **kwargs):
        # Autodeletes after a delay
        channel = await self.bot.fetch_channel(c_id)
        message = await channel.send(**kwargs)
        if is_finite(delete_after):
            create_task(self.bot.silent_delete(message, no_log=True, delay=delete_after))

    async def _typing_(self, user, channel, **void):
        # Typing indicator for DM channels
        bot = self.bot
        if user.id == bot.client.user.id or bot.is_blacklisted(user.id):
            return
        if not hasattr(channel, "guild") or channel.guild is None:
            emb = discord.Embed(colour=rand_colour())
            emb.set_author(name=f"{user} ({user.id})", icon_url=best_url(user))
            emb.description = italics(ini_md("typing..."))
            for c_id, flag in self.data.items():
                if flag & 2:
                    create_task(self.sendDeleteID(c_id, embed=emb))

    def prepare_string(self, s, lim=2000, fmt="py"):
        if type(s) is not str:
            s = str(s)
        if s:
            if not s.startswith("```") or not s.endswith("```"):
                return lim_str("```" + fmt + "\n" + s + "```", lim)
            return lim_str(s, lim)
        return "``` ```"

    # Only process messages that were not treated as commands
    async def _nocommand_(self, message, **void):
        bot = self.bot
        channel = message.channel
        if bot.is_owner(message.author.id) and channel.id in self.data:
            flag = self.data[channel.id]
            # Both main and virtual terminals may be active simultaneously
            for f in (flag & 1, flag & 4):
                if f:
                    proc = message.content.strip()
                    if proc:
                        # Ignore commented messages
                        if proc.startswith("//") or proc.startswith("||") or proc.startswith("\\") or proc.startswith("#"):
                            return
                        if proc.startswith("`") and proc.endswith("`"):
                            if proc.startswith("```"):
                                proc = proc[3:]
                                spl = proc.splitlines()
                                if spl[0].isalnum():
                                    spl.pop(0)
                                proc = "\n".join(spl)
                            proc = proc.strip("`")
                        if not proc:
                            return
                        with suppress(KeyError):
                            # Write to input() listener if required
                            if self.listeners[channel.id] is None:
                                create_task(message.add_reaction("👀"))
                                self.listeners[channel.id] = proc
                                return
                        if not proc:
                            return
                        proc = proc.translate(self.qtrans)
                        try:
                            create_task(message.add_reaction("❗"))
                            result = await self.procFunc(proc, channel, bot, term=f)
                            output = str(result)
                            if len(output) > 54000:
                                f = discord.File(io.BytesIO(output.encode("utf-8")), filename="message.txt")
                                await bot.send_with_file(channel, "Response over 54,000 characters.", file=f, filename="message.txt")
                            elif len(output) > 1993:
                                bot.send_as_embeds(channel, output, md=code_md)
                            else:
                                await channel.send(self.prepare_string(output, fmt=""))
                        except:
                            await send_with_react(channel, self.prepare_string(traceback.format_exc()), reacts="❎")
        # Relay DM messages
        elif message.guild is None:
            if bot.is_blacklisted(message.author.id):
                return await send_with_react(channel,
                    "Your message could not be delivered because you don't share a server with the recipient or you disabled direct messages on your shared server, "
                    + "recipient is only accepting direct messages from friends, or you were blocked by the recipient.",
                    reacts="❎"
                )
            user = message.author
            emb = discord.Embed(colour=discord.Colour(16777214))
            emb.set_author(name=f"{user} ({user.id})", icon_url=best_url(user))
            emb.description = message_repr(message)
            invalid = deque()
            for c_id, flag in self.data.items():
                if flag & 2:
                    channel = self.bot.cache.channels.get(c_id)
                    if channel is not None:
                        self.bot.send_embeds(channel, embed=emb)

    # All logs that normally print to stdout/stderr now send to the assigned log channels
    def _log_(self, msg, **void):
        msg = msg.strip()
        if msg:
            invalid = set()
            for c_id, flag in self.data.items():
                if flag & 8:
                    channel = self.bot.cache.channels.get(c_id)
                    if channel is None:
                        invalid.add(c_id)
                    else:
                        self.bot.send_as_embeds(channel, msg, colour=(xrand(6) * 256), md=code_md)
            [self.data.pop(i) for i in invalid]

    def _bot_ready_(self, **void):
        with suppress(AttributeError):
            PRINT.funcs.append(self._log_)

    def _destroy_(self, **void):
        with suppress(LookupError, AttributeError):
            PRINT.funcs.remove(self._log_)


class DownloadServer(Command):
    name = ["SaveServer", "ServerDownload"]
    min_level = nan
    description = "Downloads all posted messages in the target server into a sequence of .txt files."
    usage = "<server_id(curr)>"
    flags = "f"
    _timeout_ = 512
    
    async def __call__(self, bot, argv, flags, channel, guild, **void):
        if "f" not in flags:
            return bot.dangerous_command
        if argv:
            g_id = verify_id(argv)
            guild = await bot.fetch_guild(g_id)
        with discord.context_managers.Typing(channel):
            send = channel.send

            # Create callback function to send all results of the guild download.
            async def callback(channel, messages, **void):
                b = bytes()
                fn = str(channel) + " (" + str(channel.id) + ")"
                for i, message in enumerate(messages, 1):
                    temp = ("\n\n" + message_repr(message, username=True)).encode("utf-8")
                    if len(temp) + len(b) > 8388608:
                        await send(file=discord.File(io.BytesIO(b), filename=fn + ".txt"))
                        fn += "_"
                        b = temp[2:]
                    else:
                        if b:
                            b += temp
                        else:
                            b += temp[2:]
                    if not i & 8191:
                        await asyncio.sleep(0.2)
                if b:
                    await send(file=discord.File(io.BytesIO(b), filename=fn + ".txt"))

            await self.bot.database.counts.getGuildHistory(guild, callback=callback)
        response = uni_str("Download Complete.")
        return bold(ini_md(sqr_md(response)))


class Trust(Command):
    name = ["Untrust"]
    min_level = nan
    description = "Adds or removes a server from the bot's trusted server list."
    usage = "<server_id(curr)(?a)> <enable(?e)> <disable(?d)>"
    flags = "aed"

    def __call__(self, bot, flags, message, guild, argv, **void):
        update = bot.database.trusted.update
        if "a" in flags:
            guilds = bot.client.guilds
        else:
            if argv:
                g_id = verify_id(argv)
                guild = cdict(id=g_id)
            guilds = [guild]
        if "e" in flags:
            create_task(message.add_reaction("❗"))
            for guild in guilds:
                bot.data.trusted[guild.id] = True
            update()
            return css_md(f"Successfully added {sqr_md(', '.join(str(guild) for guild in guilds))} to trusted list.")
        elif "d" in flags:
            create_task(message.add_reaction("❗"))
            for guild in guilds:
                bot.data.trusted.pop(guild.id, None)
            update()
            return fix_md(f"Successfully removed {sqr_md(guild)} from trusted list.")
        return css_md(f"Trusted server list: {list(no_md(bot.cache.guilds.get(g, g)) for g in bot.data.trusted)}.")


class UpdateTrusted(Database):
    name = "trusted"


class Suspend(Command):
    name = ["Block", "Blacklist"]
    min_level = nan
    description = "Prevents a user from accessing ⟨MIZA⟩'s commands. Overrides <perms>."
    usage = "<0:user> <disable(?d)>"
    flags = "aed"

    async def __call__(self, bot, user, guild, args, flags, **void):
        update = self.data.blacklist.update
        if len(args) >= 1:
            user = await bot.fetch_user(verify_id(args[0]))
            if "d" in flags:
                bot.data.blacklist.discard(user.id)
                update()
                return css_md(f"{sqr_md(user)} has been removed from the blacklist.")
            if "a" in flags or "e" in flags:
                bot.data.blacklist.add(user.id)
                update()
                return css_md(f"{sqr_md(user)} has been added to the blacklist.")
            susp = bot.is_blacklisted(user.id)
            return css_md(f"{sqr_md(user)} is currently {'not' if not susp else ''} blacklisted.")
        return css_md(f"User blacklist: {no_md(list(bot.cache.users.get(u, u) for u in bot.data.blacklist))}")


class UpdateBlacklist(Database):
    name = "blacklist"
    no_delete = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if type(self.data) is not set:
            self.bot.data[self.name] = self.data = set(self.data)


class UpdateEmojis(Database):
    name = "emojis"
    no_delete = True

    def get(self, name):
        while not self.bot.bot_ready:
            time.sleep(2)
        with suppress(KeyError):
            return self.bot.cache.emojis[self.data[name]]
        guild = self.bot.get_available_guild()
        with open(f"misc/emojis/{name}", "rb") as f:
            emoji = await_fut(guild.create_custom_emoji(name="_m", image=f.read()))
            self.data[name] = emoji.id
            self.update()
        self.bot.cache.emojis[emoji.id] = emoji
        return emoji

    def create_progress_bar(self, length, ratio):
        start_bar = [str(self.get(f"start_bar_{i}.gif")) for i in range(5)]
        mid_bar = [str(self.get(f"mid_bar_{i}.gif")) for i in range(5)]
        end_bar = [str(self.get(f"end_bar_{i}.gif")) for i in range(5)]
        high = length * 4
        position = min(high, round(ratio * high))
        items = deque()
        new = min(4, position)
        items.append(start_bar[new])
        position -= new
        for i in range(length - 1):
            new = min(4, position)
            if i >= length - 2:
                bar = end_bar
            else:
                bar = mid_bar
            items.append(bar[new])
            position -= new
        return "".join(items)