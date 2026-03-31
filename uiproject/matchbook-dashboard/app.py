from flask import Flask, render_template, request, jsonify
import json, threading, time, os, sys, logging, re
from scraper import launch_bot
from datetime import datetime, date
import subprocess

# ✅ FIRST DEFINE APP
app = Flask(__name__, template_folder='templates')

# -------------------------
# NOW YOUR CONFIG
# -------------------------

PLAYBET_CONFIG_FILE = "playconfig.json"

playbet_bot_process = None

@app.route("/api/playbet/config")
def playbet_get_config():
    return jsonify(playbet_load_config())

def playbet_load_config():
    if not os.path.exists(PLAYBET_CONFIG_FILE):
        return {"bot_running": False, "mode": "dry", "users": []}

    with open(PLAYBET_CONFIG_FILE) as f:
        return json.load(f)


def playbet_save_config(data):
    with open(PLAYBET_CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ================= GET USERS =================
@app.route("/api/playbet/users")
def playbet_get_users():
    config = playbet_load_config()
    return jsonify(config["users"])


# ================= ADD USER =================
@app.route("/api/playbet/user", methods=["POST"])
def playbet_add_user():

    data = request.json
    config = playbet_load_config()

    user = {
        "username": data.get("username"),
        "password": data.get("password"),
        "sport": data.get("sport"),
        "side": data.get("side"),
        "total": data.get("total"),
        "before_game": data.get("before_game"),
        "mode": data.get("mode", "dry"),
        "status": data.get("status", "active")
    }

    config["users"].append(user)

    playbet_save_config(config)

    return {"status": "user added"}


# ================= UPDATE USER =================
@app.route("/api/playbet/user/<username>", methods=["PUT"])
def playbet_update_user(username):

    data = request.json
    config = playbet_load_config()

    for i, u in enumerate(config["users"]):
        if u["username"] == username:
            config["users"][i] = data
            break

    playbet_save_config(config)

    return {"status": "updated"}


# ================= DELETE USER =================
@app.route("/api/playbet/user/<username>", methods=["DELETE"])
def playbet_delete_user(username):

    config = playbet_load_config()

    config["users"] = [
        u for u in config["users"]
        if u["username"] != username
    ]

    playbet_save_config(config)

    return {"status": "deleted"}


# ================= BOT START =================
@app.route("/api/playbet/bot/start")
def playbet_start_bot():

    global playbet_bot_process

    if playbet_bot_process is None:

        playbet_bot_process = subprocess.Popen(
            ["node", "LV152.js"]
        )

        config = playbet_load_config()
        config["bot_running"] = True
        playbet_save_config(config)

    return {"status": "bot started"}


# ================= BOT STOP =================
@app.route("/api/playbet/bot/stop")
def playbet_stop_bot():

    global playbet_bot_process

    if playbet_bot_process:
        playbet_bot_process.terminate()
        playbet_bot_process = None

    config = playbet_load_config()
    config["bot_running"] = False
    playbet_save_config(config)

    return {"status": "bot stopped"}


# ================= BOT MODE =================
@app.route("/api/playbet/mode", methods=["POST"])
def playbet_set_mode():

    data = request.json

    config = playbet_load_config()

    config["mode"] = data["mode"]

    playbet_save_config(config)

    return {"status": "mode updated"}

CONFIG_FILE = "config.json"
BETS_FILE = "bets.json"
config_lock = threading.Lock()

# -------------------------------------------------------
# BOT STATUS + LOG SYSTEM
# -------------------------------------------------------
bot_status = {"running": False, "last_log": "", "start_time": None}
bot_thread = None
stop_flag_ref = {"stop": False}
LOGS = []  # memory logs

# -------------------------------------------------------
# ADD LOG FUNCTION
# -------------------------------------------------------
def add_log(text):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    LOGS.append(f"[{ts}] {text}")
    if len(LOGS) > 5000:
        LOGS.pop(0)

def normalize_player(p):
    if not p:
        return None
    return p.split(" ")[0]   # PIR695 (xxxx) → PIR695

# -------------------------------------------------------
# STREAM STDOUT/STDERR TO LOGS
# -------------------------------------------------------
ACCESS_LOG_1 = re.compile(r'^\d+\.\d+\.\d+\.\d+ - - \[.+?\] ".*HTTP/1\.[01]".*$')
ACCESS_LOG_2 = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[INFO\] \d+\.\d+\.\d+\.\d+ - - \[.+?\] ".*HTTP/1\.[01]".*$')

class StreamToLogs:
    def __init__(self):
        self._stdout = sys.stdout

    def write(self, message):
        msg = message.strip()
        if msg:
            # Skip all access logs
            if ACCESS_LOG_1.match(msg) or ACCESS_LOG_2.match(msg):
                return
            add_log(msg)
        self._stdout.write(message + "\n")

    def flush(self):
        self._stdout.flush()

sys.stdout = StreamToLogs()
sys.stderr = StreamToLogs()

# -------------------------------------------------------
# Capture Flask logs
# -------------------------------------------------------
class LogHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        # ignore access logs
        if ACCESS_LOG_1.match(log_entry) or ACCESS_LOG_2.match(log_entry):
            return
        add_log(log_entry)
        print(log_entry)

flask_logger = logging.getLogger('werkzeug')
flask_logger.setLevel(logging.ERROR)  # ignore GET/POST 200 OK
flask_logger.addHandler(LogHandler())

# -------------------------------------------------------
# CONFIG LOAD/SAVE
# -------------------------------------------------------
def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {"rules": {}, "allowed_players_with_sports": {}}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(data):
    with config_lock:
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)

# -------------------------------------------------------
# BET STORAGE
# -------------------------------------------------------
def load_bets():
    try:
        with open(BETS_FILE) as f:
            data = json.load(f)
            # ensure it's always a list
            if isinstance(data, dict) and "bets" in data:
                return data["bets"]
            elif isinstance(data, list):
                return data
            else:
                return []
    except Exception as e:
        print("[LOAD BETS ERROR]", e)
        return []

# -------------------------------------------------------
# ROUTES
# -------------------------------------------------------
@app.route("/")
def dashboard():
    cfg = load_config()
    return render_template(
        "dashboard.html",
        rules=cfg.get("rules", {}),
        allowed=cfg.get("allowed_players_with_sports", {}),
        live_mode=cfg.get("live_mode", False)
    )
@app.route("/play-bet-now")
def play_bet_now():
    return render_template("playbetnow.html")

@app.route("/toggle_live_mode", methods=["POST"])
def toggle_live_mode():
    cfg = load_config()
    cfg["live_mode"] = not cfg.get("live_mode", False)
    save_config(cfg)
    return jsonify({"status": "ok", "live_mode": cfg["live_mode"]})

# -------------------------------------------------------
# LOGS
# -------------------------------------------------------
@app.route("/logs")
def get_logs():
    return jsonify(LOGS[-100:])

# -------------------------------------------------------
# STATS
# -------------------------------------------------------

@app.route("/stats")
def stats():
    bets = load_bets() or []
    today = date.today()

    today_bets = []
    for b in bets:
        ts = b.get("timestamp")
        if not ts:
            continue
        try:
            bet_date = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").date()
            if bet_date == today:
                today_bets.append(b)
        except:
            continue

    total_bets = len(today_bets)

    active_players = len({
        normalize_player(b.get("player"))
        for b in today_bets
        if normalize_player(b.get("player"))
    })

    last_bet = "—"
    if today_bets:
        last_bet = max(
            today_bets,
            key=lambda b: b.get("timestamp", "")
        ).get("timestamp", "—")

    return jsonify({
        "active_players": active_players,
        "total_bets": total_bets,
        "last_bet": last_bet
    })


# -------------------------------------------------------
# START BOT
# -------------------------------------------------------
@app.route("/start_bot", methods=["POST"])
def start_bot_route():
    global bot_thread, stop_flag_ref, bot_status
    if bot_thread and bot_thread.is_alive():
        return jsonify({"status": "already_running"})
    stop_flag_ref = {"stop": False}

    def run_bot():
        bot_status["running"] = True
        bot_status["start_time"] = time.time()
        bot_status["last_log"] = "Bot started"
        add_log("Bot started")
        try:
            launch_bot(stop_flag_ref)
        except Exception as e:
            bot_status["last_log"] = f"Bot crashed: {str(e)}"
            add_log(f"Bot crashed: {str(e)}")
        finally:
            bot_status["running"] = False
            bot_status["last_log"] = "Bot stopped"
            add_log("Bot stopped")

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    return jsonify({"status": "started"})

@app.route("/stop_bot", methods=["POST"])
def stop_bot_route():
    global stop_flag_ref
    stop_flag_ref["stop"] = True
    return jsonify({"status": "stopping"})

@app.route("/bot_status")
def get_bot_status():
    return jsonify({"status": bot_status})

# -------------------------------------------------------
# RULES
# -------------------------------------------------------
@app.route("/delete_rule_row", methods=["POST"])
def delete_rule_row():
    data = request.get_json() or {}
    player = data.get("player")
    sport = data.get("sport")
    game_type = data.get("game_type")
    if not player or not sport or not game_type:
        return jsonify({"error": "missing fields"}), 400

    cfg = load_config()
    rules = cfg.setdefault("rules", {})
    if player in rules and sport in rules[player]:
        gt = rules[player][sport].get("game_types", {})
        if game_type in gt:
            del gt[game_type]
        if not gt:
            del rules[player][sport]
        if not rules[player]:
            del rules[player]

    save_config(cfg)
    add_log(f"Rule deleted: {player} → {sport} → {game_type}")
    return jsonify({"status": "deleted"})

@app.route("/update_rule_row", methods=["POST"])
def update_rule_row():
    data = request.get_json() or {}
    player = data.get("player")
    sport = data.get("sport")
    game_types = data.get("game_types", {})
    old_player = data.get("old_player")
    old_sport = data.get("old_sport")
    old_game_type = data.get("old_game_type")

    if not player or not sport:
        return jsonify({"error": "missing fields"}), 400

    cfg = load_config()
    rules = cfg.setdefault("rules", {})

    # --- DELETE OLD ENTRY IF RENAMED ---
    if old_player and old_sport and old_game_type:
        try:
            del rules[old_player][old_sport]["game_types"][old_game_type]
        except KeyError:
            pass
        # Clean up empty nested dicts
        if old_player in rules and old_sport in rules[old_player]:
            if not rules[old_player][old_sport]["game_types"]:
                del rules[old_player][old_sport]
        if old_player in rules and not rules[old_player]:
            del rules[old_player]

    # --- CREATE/UPDATE NEW ENTRY ---
    rules.setdefault(player, {})
    rules[player].setdefault(sport, {"game_types": {}})

    for gt, vals in game_types.items():
        rules[player][sport]["game_types"][gt] = {
            "value": int(vals.get("value", 0) or 0),
            "line": float(vals.get("line", 0) or 0),
            "price": float(vals.get("price", 0) or 0),
            "time_restriction": vals.get("time_restriction") or []
        }

    save_config(cfg)
    add_log(f"Rule updated: {player} → {sport}")
    return jsonify({"status": "saved"})

# -------------------------------------------------------
# RUN FLASK
# -------------------------------------------------------
if __name__ == "__main__":
    print("[CONFIG] loaded rules={} allowed_players={}".format(
        len(load_config().get("rules", {})),
        len(load_config().get("allowed_players_with_sports", {}))
    ))
    app.run(host="0.0.0.0", port=5000, debug=True)
