#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, time, random, logging, datetime, requests, hmac, hashlib, base64, uuid, json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.options import Options

# ========= Logging =========
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
def log(*args, **kwargs):
    print(*args, **kwargs, flush=True)

# ========= Telegram =========
SEND_TELEGRAM = True
TELEGRAM_BOT_TOKEN = '8085892705:AAE5qdmuoT-6qDoGl3DVYy-7bvf0ITz0AeA'
TG_CHATS = ['6695038579', '1704264194']

def send_telegram_message(message: str):
    if not SEND_TELEGRAM or not message:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [message[i:i+3500] for i in range(0, len(message), 3500)]
    for chat_id in TELEGRAM_CHAT_IDS:
        for part in chunks:
            try:
                requests.post(url, data={"chat_id": chat_id, "text": part}, timeout=10)
            except Exception as e:
                logging.warning(f"[TG] {chat_id}: {e}")

# ========= AOS API =========
AOS_BASE_URL    = "http://72.52.240.29:9000/api/v1/bets/place"
AOS_PUBLIC_KEY  = "B144C74C-89FD-4767-9756-76034DB71D1F"
AOS_PRIVATE_KEY = "BD9FBD5A00A83203C64238227B3349EE"
AOS_PROFILE_ID  = 963
AOS_LIMIT       = 0
AOS_POST_LIVE   = False

def _key_bytes(secret: str) -> bytes:
    return (secret or "").encode("utf-8")

def _fmt_line_for_sig(val) -> str:
    try: x = float(val or 0.0)
    except: x = 0.0
    return "0.0" if x==0.0 else ("%g" % x)

def _fmt_line_json(val):
    try:
        x = float(val or 0.0)
        return int(round(x)) if abs(x-round(x))<1e-9 else float(("%g"%x))
    except: return 0

def _is_top_team(rot: int) -> bool:
    return (rot % 2) == 1

def _infer_period(selection: str, sport: str) -> int:
    s = (selection or "").lower()
    if "first half" in s or "1h" in s: return 1
    if "second half" in s or "2h" in s: return 2
    if "1st quarter" in s or re.search(r"\bq1\b", s): return 3
    if "2nd quarter" in s or re.search(r"\bq2\b", s): return 4
    if "3rd quarter" in s or re.search(r"\bq3\b", s): return 5
    if "4th quarter" in s or re.search(r"\bq4\b", s): return 6
    return 0

def _infer_side(selection: str, ou: str, is_top: bool) -> int:
    s = (selection or "").lower()
    if "total" in s:
        if (ou or "").upper().startswith("O"): return 2
        if (ou or "").upper().startswith("U"): return -2
    return 3 if is_top else -3

def place_aos_bet(rot, selection, sport, ou, pts_val, odds_txt, player="", scheduled="", attempts=1, delay_between=0.5):
    results=[]
    try:
        period = _infer_period(selection, sport)
        is_top = _is_top_team(rot)
        side = _infer_side(selection, ou, is_top)
        base_line = pts_val
        base_price = int(odds_txt or 0)

        for attempt in range(1, attempts+1):
            line_val = base_line
            price = base_price
            signed_string = f"{rot}{AOS_PROFILE_ID}{AOS_LIMIT}{period}{side}{_fmt_line_for_sig(line_val)}{price}"
            digest = hmac.new(_key_bytes(AOS_PRIVATE_KEY), signed_string.encode("utf-8"), hashlib.sha256).digest()
            sig_b64 = base64.b64encode(digest).decode("utf-8")
            payload = {
                "rot": int(rot),
                "profile": int(AOS_PROFILE_ID),
                "limit": int(AOS_LIMIT),
                "period": int(period),
                "side": int(side),
                "line": _fmt_line_json(line_val),
                "price": int(price),
            }
            req_id = str(uuid.uuid4())
            url = f"{AOS_BASE_URL}/{req_id}"
            headers = {"Content-Type":"application/json;charset=utf-8","X-AUTH": f"key={AOS_PUBLIC_KEY},hash={sig_b64}"}

            print(f"=== LIVE (attempt {attempt}/{attempts}) ===")
            print("Payload:", json.dumps(payload, indent=2))

            if AOS_POST_LIVE:
                try:
                    resp = requests.post(url, headers=headers, json=payload, timeout=10)
                    send_telegram_message(f"[AOS LIVE] player={player} rot={rot} status={resp.status_code}")
                    results.append({"attempt": attempt, "ok": resp.ok, "status": resp.status_code})
                except Exception as e:
                    send_telegram_message(f"[AOS ERROR] player={player} rot={rot} error={e}")
                    results.append({"attempt": attempt, "ok": False, "error": str(e)})
            if attempt<attempts: time.sleep(delay_between)
        return {"ok": any(r.get("ok") for r in results), "status":"MULTI_ATTEMPT","results":results}
    except Exception as e:
        send_telegram_message(f"AOS error {e}")
        logging.exception("place_aos_bet error")
        return {"ok": False, "status":"ERROR","detail":str(e)}

# ========= Helpers =========
scraped_tickets = set()
def _convert_points(txt: str | None):
    if not txt: return None
    try:
        s = str(txt).strip()
        if not s: return None
        return float(s.replace("½",".5").replace("+",""))
    except: return None

def _expand_and_get_row_detail(driver, row, team_info, ticket_number, timeout=6):
    detail_xpath = f".//div[contains(@class,'wager-side-description') and @id='{ticket_number}-1']"
    detail_div = row.find_element(By.XPATH, detail_xpath)
    def visible(): return ("Invisible" not in (detail_div.get_attribute("class") or "")) and detail_div.is_displayed()
    if not visible():
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", team_info)
        try: team_info.click()
        except: driver.execute_script("arguments[0].click();", team_info)
        WebDriverWait(driver, timeout).until(lambda d: visible())
    time.sleep(0.15)
    return detail_div

# ========= Scrape function =========
def scrape_table_once(driver):
    import pytz
    def is_overnight_et():
        et = pytz.timezone("US/Eastern")
        now_et = datetime.datetime.now(et)
        return now_et.hour>=22 or now_et.hour<9
    overnight_players={"pir695","pir743"}
    def is_overnight_bet(player,sport,selection):
        return (player.lower() in overnight_players and sport.lower()=="basketball - nba" and "total" in (selection or "").lower())
    rows=driver.find_elements(By.CSS_SELECTOR,"tr.wager-detail-info")
    if not rows: return
    for idx,row in enumerate(rows):
        try:
            cust_td=row.find_element(By.CSS_SELECTOR,"td.LineCellMonitorFirstRow.d-none.d-xl-table-cell")
            header=cust_td.text.strip()
            m=re.search(r"\((\d+)\)",header)
            ticket=m.group(1) if m else "N/A"
            player=header.split("(")[0].strip().split()[0] if header else "N/A"
            if ticket in scraped_tickets: continue
            scraped_tickets.add(ticket)
            mid_td=row.find_elements(By.CSS_SELECTOR,"td.LineCellMonitorFirstRow")[1]
            team_info=mid_td.find_element(By.CSS_SELECTOR,"div.DivDetailTicketShort")
            rotation=team_info.find_element(By.CSS_SELECTOR,"font.TeamRotationTicket").text.strip()
            teams=team_info.find_element(By.CSS_SELECTOR,"font.TeamNameTicket").text.strip()
            odds=team_info.find_element(By.CSS_SELECTOR,"font.OddsTicket").text.strip()
            pts_text=team_info.find_element(By.CSS_SELECTOR,"font.PointsTicket").text.strip()
            ou="N/A"
            for tgt in team_info.find_elements(By.CSS_SELECTOR,"font.TargetTicket"):
                t=tgt.text.strip().lower()
                if t in ("o","u"): ou=t.upper(); break
            pts_val=_convert_points(pts_text)
            tds=row.find_elements(By.CSS_SELECTOR,"td.LineCellMonitorFirstRow")
            taken=tds[3].text.strip() if len(tds)>3 else "N/A"
            if taken.strip().upper()=="ATW": continue
            details=_expand_and_get_row_detail(driver,row,team_info,ticket,timeout=6)
            def safe(sel):
                try: return details.find_element(By.CSS_SELECTOR,sel).text.strip()
                except NoSuchElementException: return "N/A"
            sport=safe("#sportleague-value")
            selection=safe("#selection-value")
            scheduled=safe("#scheduledDateTime-value")
            if ou=="N/A":
                low_sel=(selection or "").lower()
                if " over" in low_sel or low_sel.startswith("over"): ou="O"
                elif " under" in low_sel or low_sel.startswith("under"): ou="U"
            if is_overnight_et() and is_overnight_bet(player,sport,selection):
                line_move=0.5; price_move=20
                pts_val=pts_val or 0.0
                line_val=pts_val+line_move if ou=="O" else pts_val-line_move
                base_price=price_move
                try:
                    rot_int=int(re.search(r"\d+",rotation).group(0))
                    resp=place_aos_bet(rot_int,selection,sport,ou,line_val,str(base_price),player=player,scheduled=scheduled)
                except Exception as e: resp={"status":f"Error: {e}"}
                msg=(f"🌙 *OVERNIGHT NBA TOTAL*\nPlayer: {player}\nSport: {sport}\nTeams: {teams}\n"
                     f"Selection: {selection}\nRotation: {rotation}\nLine: {pts_val}->{line_val} | OU: {ou}\n"
                     f"Price: {base_price}\nAOS Status: {resp.get('status')}\n────────────────────────────")
                print("\n"+msg)
                send_telegram_message(msg)
        except Exception as e:
            logging.warning(f"[SCRAPE] row {idx+1} error: {e}")

# ========= Browser =========
def human_delay(lo: float, hi: float): time.sleep(random.uniform(lo, hi))

def launch_driver_simple():
    chrome_options = Options()
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--start-maximized")
    driver = webdriver.Chrome(options=chrome_options)
    driver.implicitly_wait(10)
    log("[BROWSER] Chrome ready (simple launch)")
    return driver

# ========= Login =========
def login_and_navigate(driver) -> bool:
    """Login to BetWar, open Monitor2, simulate reading scrolls, and uncheck bet-type boxes."""
    try:
        log("[NAV] opening login page…")
        driver.get("https://betwar.com/Logins/039/sites/betwar/index.aspx")
        human_delay(1, 2)

        # ---------- Login ----------
        access = driver.find_element(By.CSS_SELECTOR, "#txtAccessOfCode")
        for ch in "spades": 
            access.send_keys(ch)
            human_delay(0.06, 0.18)

        pwd = driver.find_element(By.CSS_SELECTOR, "#txtAccessOfPassword")
        for ch in "PS17$": 
            pwd.send_keys(ch)
            human_delay(0.06, 0.18)

        btn = driver.find_element(By.XPATH, "//input[@type='submit']")
        ActionChains(driver).move_to_element(btn).click().perform()
        human_delay(4, 6)

        # ---------- Open Monitor ----------
        driver.get("https://betwar.com/Agent/Monitor2.aspx")
        human_delay(2, 4)
        log("[NAV] ready on monitor page")

        # ---------- Human scroll simulation ----------
        log("[NAV] simulating reading scrolls before unchecking boxes…")
        for _ in range(random.randint(1, 3)):
            scroll_amt = random.randint(100, 400)
            driver.execute_script(f"window.scrollBy(0, {scroll_amt});")
            human_delay(0.3, 1.0)
            driver.execute_script(f"window.scrollBy(0, -{scroll_amt});")
            human_delay(0.3, 1.0)

        # ---------- Uncheck bet-type checkboxes ----------
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

                ActionChains(driver) \
                    .move_to_element_with_offset(
                        checkbox,
                        random.uniform(-5, 5),
                        random.uniform(-5, 5)
                    ).pause(random.uniform(0.2, 0.5)) \
                    .click() \
                    .perform()

                log(f"[NAV] unchecked checkbox {i}/{len(checkbox_xpaths)}")
                human_delay(0.4, 0.9)

            except Exception as e:
                log(f"[NAV] checkbox error {i}: {e}")

        log("[NAV] checkbox setup complete")
        return True

    except Exception as e:
        logging.error(f"login/navigation: {e}")
        return False

# ========= Continuous Loop =========
def continuous_scrape_stable(driver):
    """Scrape every 0.8 sec and restart Chrome every 15 minutes."""
    last_reconnect = datetime.datetime.now()
    chrome_interval = datetime.timedelta(minutes=15)
    tick = 0

    while True:
        try:
            tick += 1
            now = datetime.datetime.now()

            # ---- 15 min Chrome restart ----
            if now - last_reconnect >= chrome_interval:
                log("[BROWSER] Restarting Chrome (15 min interval)")
                try: driver.quit()
                except: pass
                driver = launch_driver_simple()
                if not login_and_navigate(driver): break
                last_reconnect = now

            # ---- Scrape ----
            scrape_table_once(driver)

            time.sleep(0.8)  # scrape every 0.8 seconds

        except Exception as e:
            log(f"[LOOP ERROR] {e}, restarting Chrome…")
            try: driver.quit()
            except: pass
            driver = launch_driver_simple()
            if not login_and_navigate(driver): break
            last_reconnect = datetime.datetime.now()


# ========= Entrypoint =========
def main():
    driver = None
    try:
        driver = launch_driver_simple()
        if not login_and_navigate(driver): return
        continuous_scrape_stable(driver)
    finally:
        if driver:
            try: driver.quit()
            except: pass

if __name__=="__main__":
    main()
