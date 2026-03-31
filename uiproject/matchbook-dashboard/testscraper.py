#!/usr/bin/env python3
# -- coding: utf-8 --

import os, re, time, random, zipfile, logging, datetime, requests, hmac, hashlib, base64, uuid, json
import shutil, tempfile
import undetected_chromedriver as uc
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    TimeoutException, ElementClickInterceptedException,
    NoSuchElementException, StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from collections import OrderedDict
def wait_for_details(driver):
    wait = WebDriverWait(driver, 10)
    try:
        wait.until(lambda d: safe_text(d, "#selection-value") != "")
    except TimeoutException:
        # Skip this row
        return False
    return True


# ========= Verbosity / Printing =========
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()  # DEBUG/INFO/WARNING

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
BETS_FILE = "bets.json"
def start_scraper(stop_flag):
    global stop_flag_ref
    stop_flag_ref = stop_flag
    main()

import re

def clean_player(player_text: str) -> str:
    # remove anything in brackets e.g. (192670911)
    player = re.sub(r"\s*\(.*?\)", "", player_text)
    return player.strip().casefold()

def click_row_and_wait(driver, row, timeout=5):
    """
    Click wager row to load right-side details panel
    """
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", row)
        time.sleep(0.1)

        try:
            row.click()
        except Exception:
            driver.execute_script("arguments[0].click();", row)

        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#sportleague-value"))
        )
        time.sleep(0.15)
        return True
    except TimeoutException:
        log("[CLICK] detail panel did not load")
        return False

def load_processed_bets():
    try:
        with open(BETS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("processed", []))
    except:
        return set()
def norm(text: str) -> str:
    return " ".join(text.lower().replace("-", " ").split())

def save_processed_bets(processed_set):
    try:
        with open(BETS_FILE, "w", encoding="utf-8") as f:
            json.dump({"processed": list(processed_set)}, f, indent=2)
    except Exception as e:
        log(f"[BETS SAVE ERROR] {e}")

processed_bets = load_processed_bets()

def format_bet_message(bet):
    """
    Format Telegram message like your example.
    bet is a dict: ticket, player, sport, selection, rot, line, ou, odds, accepted, scheduled
    """
    msg = (
        "🎯 New Bet Detected\n"
        f"Player: {bet['player']}\n"
        f"Sport: {bet['sport']}\n"
        f"Selection: {bet['selection']}\n"
        f"Rotation #: [{bet['rot']}]\n"
        f"Line: {bet['line']} | OU: {bet.get('ou','N/A')}\n"
        f"Odds: {bet['odds']}\n"
        f"Accepted: {bet.get('accepted','N/A')}\n"
        f"Scheduled: {bet.get('scheduled','N/A')}\n"
        "────────────────────────────"
    )
    return msg

def log(*args, **kwargs):
    """Always-on console prints for step-by-step tracing."""
    print(*args, **kwargs, flush=True)

# ========= Configs =========
PROXY_CFG = {
    "host": "geo.iproyal.com",
    "port": "12321",  # string is fine for extension
    "username": "jbg953Ex5efbFWOi",
    "password": "sEtTrh7kMRPvYD5D_country-us_session-fWWCViFT_lifetime-20m_streaming-1_skipispstatic-1"
}

USE_PROFILE = False
PROFILE_PATH = r"C:\Users\Lenovo\AppData\Local\Google\Chrome\Saher Data"  # folder used earlier
CHROME_BINARY = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# ========= Telegram =========
SEND_TELEGRAM = True
TELEGRAM_BOT_TOKEN = '8293134034:AAEBhyhTlXhsYZPR4cd6umve1nTUJz3b4_o'
TELEGRAM_CHAT_IDS = ['6695038579', '1704264194']

CONFIG_PATH = "config.json"

def load_config():
    try:
        if not os.path.exists(CONFIG_PATH):
            log(f"[CONFIG] {CONFIG_PATH} not found - using empty config")
            return {}, {}
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        rules = cfg.get("rules", {})
        allowed = cfg.get("allowed_players_with_sports", {})
        # Normalize keys to lowercase for allowed players check
        allowed = {
            k.lower(): {norm(v) for v in vals}
            for k, vals in allowed.items()
        }

        log(f"[CONFIG] loaded rules={len(rules)} allowed_players={len(allowed)}")
        return rules, allowed
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        return {}, {}

rules, allowed_players_with_sports = load_config()

def send_telegram_message(message: str):
    if not SEND_TELEGRAM or not message:
        log("[TG] skipped (disabled or empty message)")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [message[i:i+3500] for i in range(0, len(message), 3500)]
    for chat_id in TELEGRAM_CHAT_IDS:
        for part in chunks:
            try:
                resp = requests.post(
                    url,
                    data={
                        "chat_id": chat_id,
                        "text": part,
                        "parse_mode": "Markdown"
                    },
                    timeout=10
                )
                if resp.status_code != 200:
                    log(f"[TG] chat={chat_id} responded {resp.status_code}")
            except Exception as e:
                log(f"[TG] {chat_id}: {e}")

# ========= AOS Place Bet API =========
AOS_BASE_URL    = "http://72.52.240.29:9000/api/v1/bets/place"
AOS_PUBLIC_KEY  = "B144C74C-89FD-4767-9756-76034DB71D1F"
AOS_PRIVATE_KEY = "BD9FBD5A00A83203C64238227B3349EE"
AOS_PROFILE_ID  = 963
AOS_LIMIT       = 0            # 0 = market
# AOS_POST_LIVE = True  <-- REMOVE THIS

def load_live_mode():
    try:
        with open("config.json", "r") as f:
            data = json.load(f)
            # inverse logic
            return not data.get("live_mode", False)
    except:
        # agar file missing ho ya error aaye, default True
        return True

AOS_POST_LIVE = load_live_mode()
print(f"AOS_POST_LIVE = {AOS_POST_LIVE}")


def _key_bytes(secret: str) -> bytes:
    return (secret or "").encode("utf-8")

def _fmt_line_for_sig(val) -> str:
    try:
        x = float(val or 0.0)
    except Exception:
        x = 0.0
    return "0.0" if x == 0.0 else ("%g" % x)

def safe_find_text(element, selector):
    try:
        return element.find_element(By.CSS_SELECTOR, selector).text.strip()
    except Exception as e:
        log(f"[WARN] Could not find {selector}: {repr(e)}")
        return ""


def _fmt_line_payload(val):
    try:
        x = float(val or 0.0)
        if abs(x) < 1e-9:
            return 0.0
        return float(("%g" % x))
    except Exception:
        return 0.0

def _fmt_line_json(val):
    try:
        x = float(val or 0.0)
        if abs(x - round(x)) < 1e-9:
            return int(round(x))
        return float(("%g" % x))
    except Exception:
        return 0

def _infer_period(selection: str, sport: str) -> int:
    s = (selection or "").lower()
    if "first half" in s or "1h" in s or "1st half" in s: return 1
    if "second half" in s or "2h" in s or "2nd half" in s: return 2
    if "1st quarter" in s or re.search(r"\bq1\b", s): return 3
    if "2nd quarter" in s or re.search(r"\bq2\b", s): return 4
    if "3rd quarter" in s or re.search(r"\bq3\b", s): return 5
    if "4th quarter" in s or re.search(r"\bq4\b", s): return 6
    if "first 5" in s or "1st 5" in s or "1st five" in s or ("innings" in s and "5" in s): return 1
    return 0

def _is_top_team(rot: int) -> bool:
    return (rot % 2) == 1

def _infer_side(selection: str, ou: str, is_top: bool) -> int:
    s = (selection or "").lower()
    if "spread" in s:  return 1 if is_top else -1
    if "money line" in s or "moneyline" in s or re.search(r"\bml\b", s): return 3 if is_top else -3
    if "total" in s and "team" not in s:
        if not ou or ou.upper() not in {"O", "U"}:
            log(f"[SKIP] totals selection missing O/U → SKIPPING")
            return {"ok": True, "status": "SKIPPED_NO_OU"}
        if ou.upper().startswith("O"): return 2
        if ou.upper().startswith("U"): return -2

    if "team total" in s:
        if is_top:  return 5 if (ou or "").upper().startswith("O") else -5
        else:       return 6 if (ou or "").upper().startswith("O") else -6
    if "draw" in s: return 4
    return 3 if is_top else -3

def _pretty(obj) -> str:
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(obj)
def _infer_game_type(selection: str) -> str:
    """Return one of: side, total, moneyline"""
    s = (selection or "").lower()

    if "spread" in s or "side" in s:
        return "side"

    # team total skip → normal total
    if "total" in s and "team" not in s:
        return "total"

    if "moneyline" in s or "money line" in s or re.search(r"\bml\b", s):
        return "moneyline"

    # Fallback → treat as moneyline (safe default)
    return "moneyline"
def place_aos_bet(rot, selection, sport, ou, pts_val, odds_txt, player="", scheduled=""):
    log("========== place_aos_bet CALLED ==========")
    log(f"INPUT rot={rot}, selection={selection}, sport={sport}, ou={ou}, pts={pts_val}, odds={odds_txt}, player={player}, scheduled={scheduled}")
    log(f"AOS_POST_LIVE = {AOS_POST_LIVE}")

    try:
        # --- Normalize ---
        sel_lower = (selection or "").lower()
        log(f"[STEP] selection lower = {sel_lower}")

        # --- GLOBAL SKIP RULE ---
        if "second half" in sel_lower or "2h" in sel_lower or "2nd half" in sel_lower:
            log("[SKIP] 2H detected → SKIPPING BET")
            return {"ok": True, "status": "SKIPPED_2H"}

        pl = (player or "").strip().casefold()
        sp = (sport or "").strip().casefold()
        log(f"[STEP] normalized player={pl}, sport={sp}")

        # --- Default cutoff ---
        cutoff_hours = 1
        log(f"[STEP] cutoff_hours default = {cutoff_hours}")

        # --- Infer game type ---
        game_type = _infer_game_type(selection)
        log(f"[STEP] inferred game_type = {game_type}")

        # --- TIME CHECK ---
        try:
            if scheduled and "N/A" not in scheduled:
                game_dt = datetime.datetime.strptime(scheduled, "%b %d, %Y %I:%M %p")
                now_dt = datetime.datetime.now()
                cutoff = game_dt - datetime.timedelta(hours=cutoff_hours)

                log(f"[TIME] now={now_dt}, game={game_dt}, cutoff={cutoff}")

                if now_dt >= cutoff:
                    log("[SKIP] Inside cutoff window → SKIPPING")
                    return {"ok": True, "status": "SKIPPED_TIME"}
        except Exception as e:
            log(f"[ERROR] TIME CHECK FAILED: {e}")

        # --- Period / side / top ---
        period = _infer_period(selection, sport)
        is_top = _is_top_team(rot)
        side = _infer_side(selection, ou, is_top)
        log(f"[STEP] period={period}, is_top={is_top}, side={side}")

        # --- Line and price ---
        line_val = 0.0 if side in (3, -3, 4) else float(pts_val or 0.0)
        price = _fmt_price(odds_txt)
        orig_line = line_val
        orig_price = price
        log(f"[STEP] line={line_val}, price={price}")

        # --- Apply rules adjustments if any ---
        if pl in rules:
            for sport_name, sport_data in rules[pl].items():
                sport_key = sport_name.strip().casefold()
                if sport_key == sp or sport_key in sp or sp in sport_key:
                    gt_block = sport_data.get("game_types", {}).get(game_type, {})
                    line_adj = float(gt_block.get("line", 0))
                    price_adj = float(gt_block.get("price", 0))
                    line_val += line_adj
                    price += price_adj
                    log(f"[RULES] line_adj={line_adj}, price_adj={price_adj} → adjusted line={line_val}, price={price}")
                    break

        # --- Signing ---
        signed_string = (
            f"{rot}{AOS_PROFILE_ID}{AOS_LIMIT}{period}{side}"
            f"{_fmt_line_for_sig(line_val)}{price}"
        )
        log(f"[SIGN] signed_string = {signed_string}")

        digest = hmac.new(
            _key_bytes(AOS_PRIVATE_KEY),
            signed_string.encode("utf-8"),
            hashlib.sha256
        ).digest()
        sig_b64 = base64.b64encode(digest).decode("utf-8")
        log(f"[SIGN] signature = {sig_b64}")

        # --- Payload ---
        payload = {
            "rot": int(rot),
            "profile": int(AOS_PROFILE_ID),
            "limit": int(AOS_LIMIT),
            "period": int(period),
            "side": int(side),
            "line": _fmt_line_json(line_val),
            "price": int(price),
        }
        log(f"[PAYLOAD] {json.dumps(payload, indent=2)}")

        req_id = str(uuid.uuid4())
        url = f"{AOS_BASE_URL}/{req_id}"
        log(f"[REQUEST] url = {url}")

        headers = {
            "Content-Type": "application/json;charset=utf-8",
            "X-AUTH": f"key={AOS_PUBLIC_KEY},hash={sig_b64}",
        }
        log(f"[HEADERS] {headers}")

        # --- Mode ---
        if AOS_POST_LIVE:
            log("========== MODE: LIVE BET ==========")
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            log(f"[AOS RESPONSE] status={resp.status_code}")
            log(f"[AOS RESPONSE] body={resp.text[:500]}")
            status = "[AOS LIVE BET]"
        else:
            log("========== MODE: DRY RUN ==========")
            resp = None
            status = "[AOS DRY RUN]"
            log("[DRY RUN] API CALL SKIPPED")

        log("[TELEGRAM] Message sent", status)

        log("========== place_aos_bet END ==========")

        return {
            "ok": True,
            "status": "LIVE" if AOS_POST_LIVE else "DRY_RUN",
            "detail": {"req_id": req_id, "line": line_val, "price": price}
        }

    except Exception as e:
        log(f"[FATAL ERROR] {e}")
        logging.exception("place_aos_bet error")
        return {"ok": False, "status": "ERROR", "detail": str(e)}

# ========= Dedupe & optional filters =========
FILTER_BY_ALLOWED = True
scraped_tickets = set()

# ========= Browser / proxy =========
def human_delay(lo: float, hi: float): time.sleep(random.uniform(lo, hi))

def create_proxy_extension(proxy_host, proxy_port, username, password, plugin_file="proxy_auth_plugin.zip"):
    manifest_json = """
    {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Chrome Proxy",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking"
        ],
        "background": { "scripts": ["background.js"] },
        "minimum_chrome_version": "22.0.0"
    }
    """

    background_js = f"""
    var config = {{
        mode: "fixed_servers",
        rules: {{
            singleProxy: {{
                scheme: "http",
                host: "{proxy_host}",
                port: parseInt({proxy_port})
            }},
            bypassList: ["localhost"]
        }}
    }};
    chrome.proxy.settings.set({{ value: config, scope: "regular" }}, function(){{}});
    
    function cb(details) {{
        return {{
            authCredentials: {{
                username: "{username}",
                password: "{password}"
            }}
        }};
    }}

    chrome.webRequest.onAuthRequired.addListener(
        cb,
        {{ urls: ["<all_urls>"] }},
        ["blocking"]
    );
    """

    with zipfile.ZipFile(plugin_file, "w") as zp:
        zp.writestr("manifest.json", manifest_json)
        zp.writestr("background.js", background_js)

    return os.path.abspath(plugin_file)


def launch_driver_with_proxy(temp_dir_holder: dict = None):
    """
    Launch Chrome using undetected_chromedriver with:
     - proxy extension for authentication
     - optional use of real profile or temporary profile
    """
    log("[BROWSER] launching Chrome with proxy…")

    ext_path = create_proxy_extension(PROXY_CFG["host"], PROXY_CFG["port"], PROXY_CFG["username"], PROXY_CFG["password"])
    uc.TARGET_VERSION = 142
    options = uc.ChromeOptions()

    # If user wishes to use real profile (may fail if Chrome already running) set USE_PROFILE True.
    if USE_PROFILE and os.path.isdir(PROFILE_PATH):
        log("[BROWSER] using provided real profile:", PROFILE_PATH)
        options.add_argument(f'--user-data-dir={PROFILE_PATH}')
    else:
        # Create a temporary profile dir and store to cleanup later
        temp_profile = tempfile.mkdtemp(prefix="uc_tmp_profile_")
        if temp_dir_holder is not None:
            temp_dir_holder["tmp_profile"] = temp_profile
        log("[BROWSER] using temporary profile:", temp_profile)
        options.add_argument(f'--user-data-dir={temp_profile}')

    # Chrome binary
    if CHROME_BINARY and os.path.exists(CHROME_BINARY):
        options.binary_location = CHROME_BINARY

    # Anti-detection flags
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1280,900")

    # Add extension
    try:
        options.add_extension(ext_path)
    except Exception as e:
        logging.warning(f"[BROWSER] add_extension failed: {e}")

    # launch
    try:
        driver = uc.Chrome(version_main=142,options=options)
        driver.implicitly_wait(10)
        log("[BROWSER] Chrome launched")
    except Exception as e:
        logging.exception("[BROWSER] Chrome launch failed")
        raise

    # quick test navigate
    try:
        driver.get("https://www.google.com")
        log("[BROWSER] test page loaded (google)")
    except Exception as e:
        logging.warning(f"[BROWSER] test page load failed: {e}")

    return driver

def normalize_line(val):
    try:
        return float(val.replace("½", ".5").replace("+", ""))
    except:
        return None


def normalize_odds(val):
    try:
        return int(val)
    except:
        return None

# ========= Site login & prep =========
def login_and_navigate(driver) -> bool:
    try:
        log("[NAV] opening login page…")
        driver.get("https://betwar.com/Logins/039/sites/betwar/index.aspx")
        time.sleep(5)   # page ko load hone ka waqt do

        # DEBUG screenshot
        driver.save_screenshot("page.png")
        log("[DEBUG] Saved screenshot: page.png")
        human_delay(1,2)
        access = driver.find_element(By.CSS_SELECTOR, "#txtAccessOfCode")
        access.click();  log("[NAV] typing access code")
        for ch in "spades": access.send_keys(ch); human_delay(0.06,0.18)
        pwd = driver.find_element(By.CSS_SELECTOR, "#txtAccessOfPassword")
        pwd.click();  log("[NAV] typing password")
        for ch in "PS17$": pwd.send_keys(ch); human_delay(0.06,0.18)
        btn = driver.find_element(By.XPATH, "//input[@type='submit']")
        log("[NAV] submitting login form")
        driver.execute_script("arguments[0].scrollIntoView(true);", btn)
        ActionChains(driver).move_to_element_with_offset(btn, random.uniform(-4,4), random.uniform(-4,4)).perform()
        human_delay(0.3,0.7); btn.click()
        human_delay(4,6)

        log("[NAV] navigating to Monitor2.aspx")
        driver.get("https://betwar.com/Agent/Monitor2.aspx")
        human_delay(2,4)

        # --- your requested scroll + checkbox uncheck block ---
        log("[NAV] simulating reading scrolls before unchecking boxes…")
        for _ in range(random.randint(1, 3)):
            scroll_amt = random.randint(100, 400)
            driver.execute_script(f"window.scrollBy(0, {scroll_amt});")
            human_delay(0.3, 1.0)
            driver.execute_script(f"window.scrollBy(0, -{scroll_amt});")
            human_delay(0.3, 1.0)

        checkbox_xpaths = [
            "/html/body/div[2]/form/div[3]/div/div[1]/div[1]/div/div/div/div[2]/label/span",  # Parlays
            "/html/body/div[2]/form/div[3]/div/div[1]/div[1]/div/div/div/div[3]/label/span",  # Reverses
            "/html/body/div[2]/form/div[3]/div/div[1]/div[1]/div/div/div/div[4]/label/span",  # Teasers
            "/html/body/div[2]/form/div[3]/div/div[1]/div[1]/div/div/div/div[5]/label/span",  # If-Bets
        ]

        log("[NAV] unchecking requested bet-type boxes…")
        for i, xpath in enumerate(checkbox_xpaths, start=1):
            try:
                checkbox = driver.find_element(By.XPATH, xpath)
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", checkbox)
                human_delay(0.3, 0.7)
                actions = ActionChains(driver)
                x_offset = random.uniform(-5, 5)
                y_offset = random.uniform(-5, 5)
                actions.move_to_element_with_offset(checkbox, x_offset, y_offset).perform()
                human_delay(0.4, 0.9)
                checkbox.click()
                log(f"[NAV] unchecked checkbox {i}/{len(checkbox_xpaths)} at {xpath}")
                human_delay(0.4, 0.9)
            except Exception as e:
                log(f"[NAV] error unchecking checkbox at {xpath}: {e}")

        log("[NAV] all specified checkboxes processed (unchecked if checked).")
        # --- end checkbox block ---

        log("[NAV] ready on monitor page")
        return True
    except Exception as e:
        logging.error(f"login/navigation: {e}")
        return False

# ========= Helpers =========
def _expand_and_get_row_detail(driver, row, team_info, ticket_number, timeout=6):
    detail_xpath = f".//div[contains(@class,'wager-side-description') and @id='{ticket_number}-1']"
    try:
        detail_div = row.find_element(By.XPATH, detail_xpath)
    except Exception:
        # fallback - try generic
        try:
            detail_div = row.find_element(By.CSS_SELECTOR, "div.wager-side-description")
        except Exception as e:
            raise

    def visible():
        cls = detail_div.get_attribute("class") or ""
        return ("Invisible" not in cls) and detail_div.is_displayed()

    if "Invisible" in (detail_div.get_attribute("class") or "") or not detail_div.is_displayed():
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", team_info)
        try:
            team_info.click()
        except Exception:
            driver.execute_script("arguments[0].click();", team_info)
        WebDriverWait(driver, timeout).until(lambda d: visible())
    time.sleep(0.15)
    return detail_div

def build_telegram_message(
    player, sport, team, selection, rot,
    line, odds, ou, accepted, scheduled
):
    return (
        "🎯 *New Bet Detected*\n"
        f"Player: {player}\n"
        f"Sport: {sport}\n"
        f"Teams: {team}\n"
        f"Selection: {selection}\n"
        f"Rotation #: [{rot}]\n"
        f"Line: {line} | OU: {ou}\n"
        f"Odds: {odds}\n"
        f"Accepted: {accepted}\n"
        f"Scheduled: {scheduled}\n"
        "────────────────"
    )


# ===== Helpers =====
def _convert_points(txt: str | None):
    if not txt: return None
    try:
        s = str(txt).strip()
        if not s: return None
        if "½" in s: return float(s.replace("½", ".5"))
        return float(s.replace("+", ""))
    except Exception:
        return None

def _fmt_price(odds_txt: str) -> int:
    s = (odds_txt or "").strip()
    s_norm = s.replace("\u2212", "-").strip().lower()
    if s_norm in {"even", "ev", "ev.", "e"}:
        return 100
    if "even" in s_norm and not re.search(r"[+\-]?\d+", s_norm):
        return 100
    m = re.search(r"[+\-]?\d+", s_norm)
    if m:
        return int(m.group(0))
    raise ValueError(f"Bad price/odds: {odds_txt!r}")

# ========= Scraper core =========
FILTER_BY_ALLOWED = True

# Keep track of already processed tickets
processed_tickets = set()

def safe_text(element, selectors):
    """
    selectors = list[str] OR single selector
    """
    if isinstance(selectors, str):
        selectors = [selectors]

    for sel in selectors:
        try:
            el = element.find_element(By.CSS_SELECTOR, sel)
            txt = el.text.strip()
            if txt:
                return txt
        except:
            continue
    return ""

def expand_row(details_div, driver):
    """Remove 'Invisible' class so hidden details render."""
    try:
        if "Invisible" in details_div.get_attribute("class"):
            driver.execute_script("arguments[0].classList.remove('Invisible')", details_div)
            time.sleep(0.05)  # short wait to render
    except:
        pass

def apply_odds_variance(odds, variance=10):
    """Apply variance to American odds: negative odds -> more negative, positive -> more positive"""
    try:
        odds_int = int(odds)  # convert string to int
    except Exception as e:
        log(f"[WARN] Could not convert odds to int: {odds}, error: {e}")
        return odds  # fallback, return as-is if conversion fails

    if odds_int < 0:
        return odds_int - variance
    elif odds_int > 0:
        return odds_int + variance
    else:
        return odds_int

def scrape_table_once(driver):
    """
    Scrape the monitor page once, place bets, send Telegram messages, and mark processed bets.
    """
    global processed_bets
    ou = "N/A"
    last_valid_sport = None
    try:
        rows = driver.find_elements(By.CSS_SELECTOR, "tr.wager-detail-info")
        total_rows = len(rows)
        log(f"[SCRAPE] found {total_rows} rows")
        if total_rows == 0:
            return []

        for idx, row in enumerate(rows):
            try:
                ticket = row.get_attribute("id").replace("tr-", "")
                player_text = safe_text(row, "td:nth-child(1)").strip()
                risk_win = safe_text(row, "td:nth-child(3)")
                taken = safe_text(row, "td:nth-child(4)")

                # --- ROT fallback safe ---
                if ticket[-2:].isdigit():
                    rot = int(ticket[-2:])
                elif ticket[-1:].isdigit():
                    rot = int(ticket[-1:])
                else:
                    rot = idx + 1

                # --- Basic line / odds from row ---
                team_name = safe_find_text(row, "font.TeamNameTicket") or "-"
                line = safe_find_text(row, "font.PointsTicket") or "0"
                odds = safe_find_text(row, "font.OddsTicket") or "100"
                ou = ou if ou else "N/A"

                # --- CLICK ROW TO LOAD DETAILS ---
                clicked = click_row_and_wait(driver, row)
                if not clicked:
                    log(f"[SKIP] row {ticket} click failed")
                    continue

                wait_for_details(driver)

                # --- SCRAPE DETAILS AFTER CLICK ---
                # Try multiple ways to get the sport name
                # --- SCRAPE DETAILS AFTER CLICK ---
                try:
                    # primary: the <strong> text
                    sport = safe_text(driver, "#sportleague-value")

                    # fallback: container text
                    if not sport or sport.strip() in ["", "-", None] or sport.isdigit():
                        sport = safe_text(driver, "#sportleague-container-value")

                    # fallback: row-based extraction
                    if not sport or sport.strip() in ["", "-"]:
                        sport = safe_find_text(row, ".sportleague-value")

                    if sport and sport.strip() not in ["", "-"]:
                        sport = sport.strip()
                        last_valid_sport = sport
                    else:
                        sport = last_valid_sport or "-"

                except Exception as e_sport:
                    log(f"[WARN] Could not get sport: {e_sport}")
                    sport = last_valid_sport or "-"



                selection = safe_text(driver, "#selection-value") or "-"
                scheduled = safe_text(driver, "#scheduledDateTime-value") or "N/A"
                teams_full = safe_text(driver, "#gameTeamNames") or team_name

                # --- ALLOWED PLAYER / SPORT CHECK ---
                if FILTER_BY_ALLOWED:
                    player_norm = clean_player(player_text)
                    sport_norm = norm(sport)
                    log("Sport name:", sport_norm, sport)
                    if player_norm not in allowed_players_with_sports:
                        log(f"[SKIP] player {player_norm} not allowed")
                        continue
                    allowed_sports = allowed_players_with_sports[player_norm]
                    if not any(a in sport_norm or sport_norm in a for a in allowed_sports):
                        log(f"[SKIP] sport {sport_norm} not allowed for player {player_norm}")
                        continue

                # --- DUPLICATE CHECK ---
                bet_id = f"{ticket}_{player_text}_{selection}"
                if bet_id in processed_bets:
                    log(f"[SKIP] bet {bet_id} already processed")
                    continue

                # --- PLACE AOS BET ---
                pts_val = risk_win.split("/")[0].strip() if "/" in risk_win else risk_win
                odds_txt = risk_win.split("/")[1].strip() if "/" in risk_win else odds
                result = place_aos_bet(
                    rot,
                    selection,
                    sport,
                    ou,
                    pts_val,
                    odds_txt,
                    player_text,
                    scheduled
                )

                # --- TELEGRAM MESSAGE ---
                if player_text:
                    detail = result.get("detail", {})

                                        # ORIGINAL (AOS / incoming)
                    orig_line = detail.get("line")
                    orig_odds = odds  # incoming / API odds

                    # FINAL (TABLE TRUSTED)
                    final_line = line
                    final_odds = odds

                    # NORMALIZE
                    orig_line_f = normalize_line(orig_line)
                    final_line_f = normalize_line(final_line)

                    orig_odds_i = normalize_odds(orig_odds)
                    final_odds_i = normalize_odds(final_odds)

                    # --- LINE VARIANCE ---
                    if orig_line_f is not None and final_line_f is not None:
                        line_var = int(final_line_f - orig_line_f)
                        line_variance_txt = f"line {orig_line}->{final_line} ({line_var:+})"
                    else:
                        line_variance_txt = f"line={final_line}"

                    # --- ODDS VARIANCE ---
                    VARIANCE_ODDS = 10  # desired variance in "cents" style


                    if final_odds_i is not None:
                        final_odds_with_var = apply_odds_variance(final_odds, VARIANCE_ODDS)
                        odds_variance_txt = f"price {final_odds}->{final_odds_with_var} ({final_odds_with_var - int(final_odds):+})"
                    else:
                        odds_variance_txt = f"price={final_odds}"


                    sport_final = sport if sport != "-" else last_valid_sport or "-"
                    final_team = team_name or teams_full
                    ou_final = ou if ou else "N/A"

                    telegram_msg = (
                        "🎯 *New Bet Detected*\n"
                        f"Player: {player_text}\n"
                        f"Sport: {sport_final}\n"
                        f"Teams: {final_team}\n"
                        f"Selection: {selection}\n"
                        f"Rotation #: [{rot}]\n"
                        f"Line: {final_line} | OU: {ou_final}\n"
                        f"Odds: {final_odds}\n"
                        f"Accepted: {taken}\n"
                        f"Scheduled: {scheduled}\n"
                        f"[{'LIVE' if AOS_POST_LIVE else 'DRY RUN'}] "
                        f"rot={rot} | {line_variance_txt} | {odds_variance_txt}\n"
                        "────────────────"
                    )

                    send_telegram_message(telegram_msg)

                else:
                    log(f"[SKIP TG] Player missing, telegram not sent. player={player_text}")

                # --- MARK PROCESSED ---
                processed_bets.add(bet_id)
                save_processed_bets(processed_bets)

            except Exception as e_row:
                log(f"[ROW {idx+1}] Error: {repr(e_row)}")
                logging.exception(e_row)
                continue

    except Exception as e:
        log(f"[SCRAPE] Fatal error: {e}")
        return []



def continuous_scrape_with_reconnect(driver):
    """
    Main loop that periodically reconnects the browser and reloads config.
    Fixed tick variable scope and safe reconnect logic.
    """
    tick = 0
    last_reconnect = datetime.datetime.now()
    interval = datetime.timedelta(minutes=15)

    log("[LOOP] starting continuous scrape loop")
    try:
        while True:
            tick += 1
            # reload config every 30 ticks
            if tick % 30 == 0:
                global rules, allowed_players_with_sports
                rules, allowed_players_with_sports = load_config()
                log("[LOOP] config reloaded")

            now = datetime.datetime.now()
            if now - last_reconnect >= interval:
                log("[LOOP] scheduled reconnect - restarting browser")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = launch_driver_with_proxy()
                if not login_and_navigate(driver):
                    log("[LOOP] re-login failed after reconnect; exiting loop")
                    break
                last_reconnect = now

            try:
                scrape_table_once(driver)
            except Exception as e:
                log(f"[LOOP] scrape_table_once exception: {e}")

            time.sleep(0.8)

    except KeyboardInterrupt:
        log("[LOOP] KeyboardInterrupt received - stopping loop")
    except Exception as e:
        log(f"[LOOP] unexpected error: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        log("[LOOP] loop ended, browser closed")



# ========= launch_bot convenience wrapper =========
def launch_bot(stop_flag_ref):
    """
    stop_flag_ref is a dict: {"stop": False}
    The bot loop checks stop_flag_ref["stop"] to exit.
    """
    driver = None
    try:
        driver = launch_driver_with_proxy()
        if not login_and_navigate(driver):
            log("[BOT] login failed, exiting")
            return

        tick = 0
        while not stop_flag_ref.get("stop", False):
            tick += 1
            try:
                scrape_table_once(driver)
            except Exception as e:
                log(f"[BOT] scrape error: {e}")
            time.sleep(0.8)

        log("[BOT] stop flag detected, exiting loop")

    except Exception as e:
        log(f"[BOT ERROR] {e}")
    finally:
        if driver:
            try: driver.quit()
            except Exception: pass
        log("[BOT] stopped gracefully")

# ========= Entrypoint =========
def main():
    log("[MAIN] Starting bot")
    driver = None
    try:
        driver = launch_driver_with_proxy()
        if not login_and_navigate(driver):
            log("[MAIN] Login failed; exiting")
            return
        continuous_scrape_with_reconnect(driver)
    except Exception as e:
        log(f"[MAIN] Exception: {e}")
    finally:
        if driver:
            try: driver.quit()
            except Exception: pass
        log("[MAIN] Bot terminated")