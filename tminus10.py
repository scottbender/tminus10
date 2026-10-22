#!/usr/bin/env python3
"""TMinus10: an IRC bot for rocket launches (Python port of tminus10.php).

Commands (in the channel):
    !launch [id|search terms]   details for the next (or a specific) launch
    !launches [1-10]            next N launches (default 3)
    !spacex [1-10]              next N SpaceX launches (default 3)
    !hype                       HYPE

Standard library only. Requires Python 3.8+.
"""

import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------- config ----
IRC_HOST = "irc.oftc.net"
IRC_PORT = 6697
NICK = "TMinus10"
CHANNEL = "#launches"
API_SERVER = "ll.thespacedevs.com"  # dev server: lldev.thespacedevs.com
API_VERSION = "2.3.0"
IGNORED_NICKS = {"Rico", "BobTheBuilder", "Old", "TMinus10", "Ahrotahntee"}
NAMES_REFRESH_SECONDS = 300  # how often to re-request the nick list

# Announce a launch (if it has a video URL) when it is this many seconds away:
# 48h 24h 12h 8h 4h 2h 1h 30m 15m 10m 5m 1m
ANNOUNCE_AT = [172800, 86400, 43200, 28800, 14400, 7200, 3600, 1800, 900, 600, 300, 60]

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# ------------------------------------------------------------ formatting ----
ANSI_FROM_IRC = [15, 0, 4, 2, 9, 1, 5, 3, 11, 10, 6, 14, 12, 13, 8, 7]


def color(code, text):
    return "\x03%02d%s\x03" % (code, text)


def irc2ansi(text):
    """Map IRC bold/colour codes to ANSI escapes (just what color() emits)."""
    out = []
    in_color = in_bold = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\x02":
            out.append("\x1b[22m" if in_bold else "\x1b[1m")
            in_bold = not in_bold
        elif ch == "\x03":
            if in_color:
                out.append("\x1b[39m")
                in_color = False
            else:
                m = re.match(r"\d{1,2}", text[i + 1:i + 3])
                n = int(m.group()) if m else 0
                out.append("\x1b[38;5;%dm" % ANSI_FROM_IRC[n % 16])
                i += 2
                in_color = True
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def seconds_to_time(seconds):
    """e.g. 93784 -> '1 day, 2 hours, 3 minutes' (at most three units)."""
    units = [
        ("year", 365 * 24 * 3600),
        ("month", 31 * 24 * 3600),
        ("week", 7 * 24 * 3600),
        ("day", 24 * 3600),
        ("hour", 3600),
        ("minute", 60),
        ("second", 1),
    ]
    if seconds == 0:
        return "0 seconds"
    parts = []
    for name, divisor in units:
        quot = int(seconds / divisor)
        if quot:
            if len(parts) == 3:
                break
            parts.append("%d %s%s" % (quot, name, "s" if abs(quot) > 1 else ""))
            seconds -= quot * divisor
    return ", ".join(parts)


def parse_ts(value):
    """ISO-8601 timestamp (with trailing Z or offset) -> epoch seconds."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def natural_key(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def make_hype_message():
    msg = "!!! "
    for i in range(2, 16):
        msg += color(i, "HYPE") + " !!! "
    return msg


# ------------------------------------------------------------------- API ----
def http_request(url):
    req = urllib.request.Request(url, headers={"User-Agent": "PHP"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # like curl, hand back the body of 4xx/5xx responses (throttle info etc.)
        return e.read().decode("utf-8", "replace")
    except Exception as e:
        print("ERROR: request: %s" % e)
        return None


def api_request(path, querystring):
    url = "https://%s/%s/%s%s" % (API_SERVER, API_VERSION, path, querystring)
    response = http_request(url)
    if response is None:
        return False
    if "<title>Server Error (500)</title>" in response:
        print("ERROR: 500 from %s" % url)
        return False
    try:
        data = json.loads(response)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        print("ERROR: could not parse json from %s" % url)
        return False
    # can fail with: {"detail":"Not found."}
    # or: {"detail":"Request was throttled. Expected available in 350 seconds."}
    if "detail" in data:
        print("ERROR: api returned error from %s" % url)
        print(" detail=%s" % data["detail"])
        return False
    return data


def get_next_launches(amount):
    data = api_request(
        "launches/upcoming/",
        "?hide_recent_previous=true&mode=detailed&limit=%d" % amount,
    )
    if data is not False and "results" in data:
        return data["results"]
    return False


def get_spacex(amount):
    data = api_request(
        "launches/upcoming/",
        "?search=SpaceX&mode=detailed&hide_recent_previous=true&limit=%d" % amount,
    )
    if data is not False and "results" in data:
        return data["results"]
    return False


# ------------------------------------------------------------------- bot ----
class Bot:
    def __init__(self):
        self.sock = None
        self.buffer = ""
        self.started = False
        self.nicks = None
        self.pending_nicks = []  # accumulates 353 lines until 366 (end of NAMES)
        self.hype_message = make_hype_message()

        self.lastping = 0
        self.lastnames = 0
        self.nextcheck = 0
        self.nextupdate = 0
        self.cached_launches = None
        self.last_cached_launches = None
        self.updates_announce_threshold = None

    # ---- connection / IO ----
    def get_connection(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            raw = socket.create_connection((IRC_HOST, IRC_PORT), timeout=5)
            self.sock = ctx.wrap_socket(raw, server_hostname=IRC_HOST)
        except OSError:
            return False
        self.sock.settimeout(0.2)  # doubles as the 200ms poll interval
        time.sleep(0.05)
        self.say("USER %s 127.0.0.1 irc.jc-mp.com :I'm a bot" % NICK)
        self.say("NICK %s" % NICK)
        self.started = False
        self.buffer = ""
        return True

    def connect_to_server(self):
        print("Connecting.. ", end="", flush=True)
        while not self.get_connection():
            print("Failed, retrying in 5 seconds..")
            time.sleep(5)
            print("Connecting.. ", end="", flush=True)
        print("Done!")

    def say(self, data, hide_from_log=False):
        self.sock.sendall((data + "\r\n").encode("utf-8"))
        if not hide_from_log:
            print(irc2ansi(data))

    def msg(self, message):
        self.say("PRIVMSG %s :%s" % (CHANNEL, message))

    # ---- launch messages ----
    def send_launch_message(self, launch, extended=False):
        seconds = int(parse_ts(launch["net"])) - int(time.time())

        text = color(7, "#%s:" % launch["id"]) + " " + color(3, launch["name"])
        if seconds > 0:
            text += " " + color(4, "in %s" % seconds_to_time(seconds))
        vid_urls = launch.get("vid_urls")
        if vid_urls:
            text += " - watch it at %s" % vid_urls[0]["url"]
        self.msg(text)

        if not extended:
            return

        mission = launch.get("mission")
        if mission:
            self.msg("%s %s" % (color(2, "Mission:"), mission.get("description") or ""))

        ws = (launch.get("window_start") or "").strip()
        we = (launch.get("window_end") or "").strip()
        if ws and we:
            if ws == we:
                self.msg("%s %s" % (color(2, "Time:"), ws))
            else:
                self.msg("%s %s - %s" % (color(2, "Window:"), ws, we))

        pad = launch.get("pad")
        if pad:
            location = pad["name"]
            loc_name = (pad.get("location") or {}).get("name")
            if loc_name:
                location += ", %s" % loc_name
            self.msg("%s %s" % (color(2, "Location:"), location))

        status = launch.get("status")
        if status:
            self.msg("%s %s" % (color(2, "Status:"), status["name"]))

    def send_update_message(self, launch, update_message):
        seconds = int(parse_ts(launch["net"])) - int(time.time())
        text = color(7, "#%s:" % launch["id"]) + " " + color(3, launch["name"])
        if seconds > 0:
            text += " " + color(4, "in %s" % seconds_to_time(seconds))
        text += ": %s" % update_message
        self.msg(text)

    # ---- periodic launch check (runs at most once per second) ----
    def check_launches(self):
        now = int(time.time())

        if now > self.nextupdate:
            info = api_request(
                "launches/upcoming/",
                "?hide_recent_previous=true&mode=detailed&limit=5",
            )
            self.updates_announce_threshold = now - 300  # 5 minutes ago
            if info is not False and "results" in info:
                self.last_cached_launches = self.cached_launches
                self.cached_launches = info["results"]

            # first API request failed and there is nothing cached: retry shortly
            if self.cached_launches is None:
                self.nextcheck = now + 5
                return

            for launch in self.cached_launches:
                launch.setdefault("updates", [])
                launch["updates"].sort(key=lambda u: parse_ts(u["created_on"]))
            self.nextupdate = now + 300

        for launch in self.cached_launches:
            seconds = int(parse_ts(launch["net"])) - now

            # post updates if the previous net time was less than 1 day away
            # to limit spam
            if self.last_cached_launches is not None:
                last_launch = next(
                    (l for l in self.last_cached_launches if l["id"] == launch["id"]),
                    None,
                )
                if last_launch is not None:
                    last_seconds = int(parse_ts(last_launch["net"])) - now - 300
                    if last_seconds < 86400:
                        self.process_updates(launch)

            if launch.get("vid_urls") and seconds in ANNOUNCE_AT:
                if seconds == 300:
                    self.msg("Hyping " + " ".join(self.nicks).strip())
                    self.msg(self.hype_message)
                self.send_launch_message(launch, False)
                if seconds == 300:
                    self.msg(self.hype_message)

        self.nextcheck = int(time.time())

    def process_updates(self, launch):
        updates_times = []
        combined = ""
        for update in launch["updates"]:
            created = int(parse_ts(update["created_on"]))
            if created > self.updates_announce_threshold:
                if combined:
                    if not re.search(r"[.!?;,]$", combined):
                        combined += ";"
                    combined += " "
                combined += update["comment"]
                updates_times.append(created)
        if combined:
            self.send_update_message(launch, combined)
        if updates_times:
            self.updates_announce_threshold = max(updates_times)

    # ---- incoming IRC lines ----
    @staticmethod
    def parse_amount(param):
        try:
            value = float(param)
        except ValueError:
            return 3
        if value < 1 or value > 10:
            return 3
        return int(value)

    def handle_line(self, data):
        e = data.split(" ")
        command = ""
        if len(e) >= 3 and e[1] == "PRIVMSG" and len(e) > 3:
            command = e[3][1:].lower()
        param = " ".join(e[4:]).strip()

        if not self.started and "MOTD" in data:
            self.say("MODE %s -hH+B" % NICK)
            time.sleep(1)
            self.say("JOIN %s" % CHANNEL)
            self.say("SAJOIN %s %s" % (NICK, CHANNEL))
            self.started = True

        # NAMES reply: ":server 353 nick = #channel :nick1 @nick2 +nick3"
        # (large channels split it over several 353 lines, ended by 366)
        if len(e) > 4 and e[1] == "353" and e[4] == CHANNEL and " :" in data:
            names = data.split(" :", 1)[1].split()
            self.pending_nicks += [n.lstrip(":~&@%+") for n in names]

        if len(e) > 3 and e[1] == "366" and e[3] == CHANNEL:
            self.nicks = sorted(
                {n for n in self.pending_nicks if n and n not in IGNORED_NICKS},
                key=natural_key,
            )
            self.pending_nicks = []

        in_channel = len(e) > 2 and e[1] == "PRIVMSG" and e[2] == CHANNEL

        if in_channel and command == "!launch":
            self.cmd_launch(param)

        if in_channel and command == "!launches":
            launches = get_next_launches(self.parse_amount(param))
            if isinstance(launches, list):
                for launch in launches:
                    self.send_launch_message(launch, False)
            else:
                self.msg("Unable to get data.")

        if in_channel and command == "!spacex":
            launches = get_spacex(self.parse_amount(param))
            if isinstance(launches, list):
                for launch in launches:
                    self.send_launch_message(launch, False)
            else:
                self.msg("Unable to get data.")

        if in_channel and command == "!hype":
            self.msg(self.hype_message)

        # someone joined/left/changed nick/was kicked: refresh the nick list
        # (":nick!user@host JOIN #chan" -> the command is the second token)
        if len(e) > 1 and e[1] in ("JOIN", "QUIT", "PART", "NICK", "KICK"):
            self.request_names()

        if e[0] == "PING":
            if len(e) > 1:
                self.say("PONG %s" % e[1])

        if len(e) > 3 and e[1] == "KICK" and e[3] == NICK:
            self.say("JOIN %s" % CHANNEL)

    def request_names(self):
        """Ask the server for the channel's nick list (answered by 353/366)."""
        self.lastnames = time.time()
        self.say("NAMES %s" % CHANNEL, True)

    def cmd_launch(self, param):
        if param:
            if UUID_RE.match(param):
                info = api_request("launches/%s/" % param, "")
            else:
                info = api_request(
                    "launches/upcoming/",
                    "?hide_recent_previous=true&mode=detailed&limit=1&search="
                    + urllib.parse.quote_plus(param),
                )
        else:
            info = api_request(
                "launches/upcoming/",
                "?hide_recent_previous=true&mode=detailed&limit=1",
            )

        launch = None
        if info is False:
            self.msg("Unable to get data.")
        elif info.get("count", 0) > 0:
            launch = info["results"][0]
        elif "id" in info:
            launch = info
        else:
            self.msg("Unable to find a launch with that ID.")

        if launch is not None:
            self.send_launch_message(launch, True)

    # ---- main loop ----
    def run(self):
        self.connect_to_server()
        while True:
            if time.time() - self.lastping > 60:
                self.lastping = time.time()
                self.say("PING :irc.jc-mp.com", True)

            # periodic refresh as a safety net (e.g. missed events, netsplits)
            if self.started and time.time() - self.lastnames > NAMES_REFRESH_SECONDS:
                self.request_names()

            if int(time.time()) > self.nextcheck and self.nicks is not None:
                self.check_launches()

            try:
                chunk = self.sock.recv(65000)
            except (socket.timeout, ssl.SSLWantReadError):
                continue
            except OSError:
                chunk = b""

            if not chunk:  # EOF: reconnect
                self.connect_to_server()
                continue

            self.buffer += chunk.decode("utf-8", "replace")
            *lines, self.buffer = self.buffer.split("\n")
            for line in lines:
                line = line.strip("\r")
                if line.strip():
                    self.handle_line(line)


if __name__ == "__main__":
    try:
        Bot().run()
    except KeyboardInterrupt:
        pass
