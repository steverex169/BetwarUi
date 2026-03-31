const { spawn } = require("child_process");

// ========================= SUPERVISOR =========================
if (!process.argv.includes("--worker")) {
  const RESTART_DELAY_MS = 3000;

  function startWorker() {
    const child = spawn(process.execPath, [__filename, "--worker"], {
      stdio: "inherit",
      env: process.env,
    });

    child.on("exit", (code, signal) => {

      // ❌ do NOT restart if stopped intentionally
      if (code === 0) {
        console.log("SUPERVISOR: Worker stopped manually. Not restarting.");
        return;
      }

      console.log(
        `${new Date().toLocaleString()} SUPERVISOR: worker crashed. Restarting in ${RESTART_DELAY_MS}ms...`
      );

      setTimeout(startWorker, RESTART_DELAY_MS);
    });
  }

  console.log(`${new Date().toLocaleString()} SUPERVISOR: starting worker...`);
  startWorker();
  return;
}

// ========================= WORKER =========================
const puppeteer = require("puppeteer-extra");
const StealthPlugin = require("puppeteer-extra-plugin-stealth");
const crypto = require("crypto");
const { v4: uuidv4 } = require("uuid");

puppeteer.use(StealthPlugin());

// ================= SETTINGS =================
const BET_TIMES = 2;
const CHECK_INTERVAL_MS = 1000;
let DRY_RUN = false;

function updateMode() {
  const config = loadConfig();
  DRY_RUN = config.mode !== "live";
}
const NAV_TIMEOUT_MS = 90_000;      // navigation + goto

const fs = require("fs");

function loadConfig() {
  try {
    const raw = fs.readFileSync("playconfig.json");
    return JSON.parse(raw);
  } catch {
    return { mode: "dry", users: [] };
  }
}

// ================= AOS CONFIG =================
const AOS_PUBLIC_KEY = "B144C74C-89FD-4767-9756-76034DB71D1F";
const AOS_PRIVATE_KEY = "BD9FBD5A00A83203C64238227B3349EE";
const AOS_PROFILE_ID = 963;
const AOS_LIMIT = 0;
const AOS_PERIOD = 0;
const AOS_ENDPOINT = "http://72.52.240.29:9000/api/v1/bets/place";

// ================= TELEGRAM =================
const TG_TOKEN = "8199694860:AAHpjmWwLBCp-EmARccYxqFcxz9a1f_496Q";
const TELEGRAM_CHAT_IDS = ["1704264194", "6599431436", "2119851963", "1640323021"];

// ================= STATE =================
const seenKeys = new Set();
let initialSyncDone = false;
let totalScraped = 0; // counts parsed plays (not raw rows)
let totalNew = 0;

// ================= UTILS =================
function time() {
  return new Date().toLocaleString();
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function signMessage(message) {
  return crypto.createHmac("sha256", AOS_PRIVATE_KEY).update(message).digest("base64");
}

async function sendTelegram(text) {
  for (const chatId of TELEGRAM_CHAT_IDS) {
    try {
      const res = await fetch(`https://api.telegram.org/bot${TG_TOKEN}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, text }),
      });

      if (!res.ok) {
        const body = await res.text().catch(() => "");
        console.log(time(), "TG ERROR", chatId, res.status, body);
      }
    } catch (e) {
      console.log(time(), "TG ERROR", chatId, e.message);
    }
  }
}

// ================= LOGIN =================
async function isLoggedOut(page) {
  try {
    return (await page.$("#username")) !== null;
  } catch {
    return true;
  }
}

async function loginAndOpen(browser, user) {
  const page = await browser.newPage();

  page.setDefaultNavigationTimeout(0);
  page.setDefaultTimeout(60000);

  await page.setUserAgent(
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122 Safari/537.36"
  );

  console.log(time(), "LOGIN", user.username);

  await page.goto("https://playbetnow.com/", { waitUntil: "networkidle2" });

  await page.type("#username", user.username);
  await page.type("#password", user.password);

  await page.click(".login-btn");

  try {
    await page.waitForNavigation({ waitUntil: "networkidle2" });
  } catch {}

  await page.goto("https://bet.playbetnow.com/wager/OpenBets.aspx", {
    waitUntil: "networkidle2",
  });

  await page.waitForSelector("table.custom_table");

  return page;
}

// ================= PARSER =================
function parsePlaybetDesc(descRaw) {
  if (!descRaw) return null;

  const desc = String(descRaw)
    .replace(/\u00BD/g, ".5")
    .replace(/\s+/g, " ")
    .trim();

  const rotMatch = desc.match(/\[(\d+)\]/);
  if (!rotMatch) return null;

  const rot = rotMatch[1];

  // -------- Extract Teams --------
  let topTeam = null;
  let bottomTeam = null;

  const teamMatch = desc.match(/\((.*?)\s+vrs\s+(.*?)\)/i);
  if (teamMatch) {
    topTeam = teamMatch[1].trim();
    bottomTeam = teamMatch[2].trim();
  }

  // -------- TOTAL --------
  if (/TOTAL/i.test(desc)) {
    const m = desc.match(/\b([ou])(\d+(\.\d)?)\b/i);
    const p = desc.match(/([+-]\d{2,3})/);
    if (!m || !p) return null;

    return {
      rot,
      type: "TOTAL",
      line: `${m[1].toLowerCase()}${m[2]}`,
      price: p[1],
      topTeam,
      bottomTeam,
      selectedTeam: null,
    };
  }

  // -------- SPREAD --------
  const spreadMatch = desc.match(
    /\[\d+\]\s*(.*?)\s*([+-]\d+(\.\d)?)\s*([+-]\d{2,3})/
  );
  if (!spreadMatch) return null;

  const selectedTeam = spreadMatch[1].trim();

  return {
    rot,
    type: "SPREAD",
    line: spreadMatch[2],
    price: spreadMatch[4],
    topTeam,
    bottomTeam,
    selectedTeam,
  };
}

// ================= VARIANCE =================
function adjustTotal(lineStr, priceStr) {
  const ou = lineStr[0];
  const line = parseFloat(lineStr.slice(1));
  const price = parseInt(priceStr, 10);

  return {
    line: ou === "u" ? line - 1 : line + 1,
    price: price - 10,
  };
}

function adjustSpread(lineStr, priceStr) {
  const line = parseFloat(lineStr);
  const price = parseInt(priceStr, 10);

  return {
    // 0.5 worse line (works for + and -)
    line: line - 0.5,
    // no price change
    price,
  };
}

// ================= SIDE RESOLUTION =================
// Prefer team-based resolution when we have (top vs bottom) AND selectedTeam text.
// Fallback to old rot odd/even logic.
function resolveSpreadSide(scraped) {
  if (scraped.topTeam && scraped.bottomTeam && scraped.selectedTeam) {
    const sel = scraped.selectedTeam.toLowerCase();
    const top = scraped.topTeam.toLowerCase();
    const bot = scraped.bottomTeam.toLowerCase();

    if (sel.includes(top)) return { side: 1, betTeam: scraped.topTeam }; // TOP
    if (sel.includes(bot)) return { side: -1, betTeam: scraped.bottomTeam }; // BOTTOM
  }

  const rotNum = parseInt(scraped.rot, 10);
  const side = rotNum % 2 === 1 ? 1 : -1;
  const betTeam =
    side === 1 ? scraped.topTeam || scraped.selectedTeam || "TOP" : scraped.bottomTeam || scraped.selectedTeam || "BOTTOM";
  return { side, betTeam };
}

// ================= AOS POST =================
async function placeBet(payload) {
  const requestId = uuidv4();

  // Signature must follow:
  // rot+profile+limit+period+side+line+price (no separators)
  const msg =
    `${payload.rot}${payload.profile}${payload.limit}` +
    `${payload.period}${payload.side}${payload.line}${payload.price}`;

  const signature = signMessage(msg);

  if (DRY_RUN) return { message: "DRY_RUN" };

  const res = await fetch(`${AOS_ENDPOINT}/${requestId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json;charset=utf-8",
      "X-AUTH": `key=${AOS_PUBLIC_KEY},hash=${signature}`,
    },
    body: JSON.stringify(payload),
  });

  const txt = await res.text();
  try {
    return JSON.parse(txt);
  } catch {
    // XAOS may return plain strings like "OK"
    return txt;
  }
}

// ================= TG FORMATTER (NO ICONS) =================
function formatTelegramMessage(scraped, payload, results, username) {

  const sideMap = { 1: "TOP", "-1": "BOTTOM", 2: "OVER", "-2": "UNDER" };

  let betTeam = "UNKNOWN";
  if (payload.side === 1 || payload.side === 2) betTeam = scraped.topTeam || "UNKNOWN";
  else betTeam = scraped.bottomTeam || scraped.topTeam || "UNKNOWN";

  const resultText = results
    .map(r => `Attempt ${r.attempt}: ${r.result?.message || r.result?.raw || r.result}`)
    .join("\n");

  return `USER
${username}

SCRAPED PLAY
Sport: ${scraped.sport}
Match: ${scraped.topTeam || "?"} vs ${scraped.bottomTeam || "?"}
ROT: ${scraped.rot}
Market: ${scraped.type}
Line: ${scraped.line}
Price: ${scraped.price}

XAOS PAYLOAD
ROT: ${payload.rot}
Bet Team: ${betTeam}
Side: ${sideMap[payload.side]}
Line: ${payload.line}
Price: ${payload.price}
Profile: ${payload.profile}
Limit: ${payload.limit}
Period: ${payload.period}

XAOS RESULTS
${resultText}`;
}

// ================= BUILD PAYLOAD =================
function buildPayload(scraped) {
  if (scraped.type === "TOTAL") {
    const adj = adjustTotal(scraped.line, scraped.price);
    return {
      rot: Number(scraped.rot),
      profile: AOS_PROFILE_ID,
      limit: AOS_LIMIT,
      period: AOS_PERIOD,
      side: scraped.line.startsWith("o") ? 2 : -2,
      line: adj.line,
      price: adj.price,
      betTeam: scraped.selectedTeam || null,
    };
  }

  const { side, betTeam } = resolveSpreadSide(scraped);
  const adj = adjustSpread(scraped.line, scraped.price);

  return {
    rot: Number(scraped.rot),
    profile: AOS_PROFILE_ID,
    limit: AOS_LIMIT,
    period: AOS_PERIOD,
    side,
    line: adj.line,
    price: adj.price,
    betTeam,
  };
}

// ================= SCRAPER LOOP (based on your logic) =================
async function scrapeLoop() {
  let browser = await puppeteer.launch({ headless: false, args: ["--no-sandbox"] });

  const config = loadConfig();
  const users = config.users;

  const sessions = [];

  for (const user of users) {
    const page = await loginAndOpen(browser, user);
    sessions.push({ page, user, initialSyncDone: false });
  }

  // crash handler
  process.on("unhandledRejection", (err) => {
    console.log(time(), "FATAL unhandledRejection:", err?.message || err);
    process.exit(1);
  });

  process.on("uncaughtException", (err) => {
    console.log(time(), "FATAL uncaughtException:", err?.message || err);
    process.exit(1);
  });

  while (true) {

    // ✅ BOT CONTROL FROM FLASK
    updateMode();
    const config = loadConfig();

    if (!config.bot_running) {
      console.log("BOT STOPPED FROM PANEL");
      process.exit(0);
    }

    try {

      for (const session of sessions) {
        const { user } = session;
        let currentPage = session.page;

        // ✅ relogin
        if (await isLoggedOut(currentPage)) {
          console.log(time(), "RELOGIN", user.username);
          currentPage = await loginAndOpen(browser, user);
          session.page = currentPage;
        }

        await currentPage.reload({ waitUntil: "networkidle0", timeout: 60000 });

        await currentPage.waitForSelector("table.custom_table", { timeout: 60000 });

        const rows = await currentPage.evaluate(() => {
          const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

          return Array.from(
            document.querySelectorAll("table.custom_table tr.TrGameOdd, tr.TrGameEven")
          )
            .map((tr) => {
              const td = tr.querySelectorAll("td");
              if (td.length < 6) return null;

              return {
                sport: clean(td[3].innerText),
                desc: clean(td[4].innerText),
              };
            })
            .filter(Boolean);
        });

        for (const r of rows) {

          const parsed = parsePlaybetDesc(r.desc);
          if (!parsed) continue;

          totalScraped++;

          const scraped = { ...parsed, sport: r.sport };

          const key = `${user.username}|${scraped.rot}|${scraped.type}|${scraped.line}|${scraped.price}|${scraped.sport}`;

          const built = buildPayload(scraped);

          const payload = {
            rot: built.rot,
            profile: built.profile,
            limit: built.limit,
            period: built.period,
            side: built.side,
            line: built.line,
            price: built.price,
          };

          // ✅ FIRST LOAD (per user)
          if (!session.initialSyncDone) {
            seenKeys.add(key);

            console.log("EXISTING SCRAPED:");
            console.log(scraped);

            console.log("EXISTING XAOS PAYLOAD:");
            console.log(payload);

            console.log("----------------------------------------");
            continue;
          }

          if (seenKeys.has(key)) continue;

          seenKeys.add(key);
          totalNew++;

          const results = [];

          for (let i = 0; i < BET_TIMES; i++) {
            try {
              results.push({
                attempt: i + 1,
                result: await placeBet(payload),
              });
            } catch (e) {
              results.push({
                attempt: i + 1,
                result: { message: `FETCH FAILED: ${e.message}` },
              });
            }
          }

          const msg = formatTelegramMessage(scraped, payload, results, user.username);

          console.log("----------------------------------------");
          console.log(msg);
          console.log("----------------------------------------");

          await sendTelegram(msg);
        }

        // ✅ mark sync complete per user
        if (!session.initialSyncDone) {
          session.initialSyncDone = true;

          console.log(time(), `INITIAL SYNC COMPLETE (${user.username})`);
        }
      }

    } catch (err) {
      console.log(time(), "ERROR:", err.message);

      try {
        await browser.close();
      } catch {}

      browser = await puppeteer.launch({ headless: false, args: ["--no-sandbox"] });

      // re-login all users
      for (const session of sessions) {
        session.page = await loginAndOpen(browser, session.user);
      }
    }

    await sleep(CHECK_INTERVAL_MS);
  }
}

// ================= MAIN =================
(async () => {
  console.log(time(), "SCRAPER STARTED");
  await scrapeLoop();
})();