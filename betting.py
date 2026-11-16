"""
Πιο πιθανά σημεία ποδοσφαίρου και μπάσκετ, χωρίς εγγραφή ή κλειδί.

Πηγές (δωρεάν, χωρίς λογαριασμό):
  Ποδόσφαιρο: football-data.co.uk – επόμενοι αγώνες ~40 πρωταθλημάτων με αποδόσεις
              Pinnacle και μέσο όρο αγοράς (1Χ2 και Over/Under 2,5). Ενημερώνεται 2 φορές την εβδομάδα.
  Μπάσκετ:    ESPN – NBA, NCAA, WNBA με αποδόσεις νίκης (moneyline).

Η πιθανότητα κάθε σημείου βγαίνει από τις αποδόσεις αφού αφαιρεθεί το περιθώριο κέρδους
της εταιρείας. Είναι η καλύτερη διαθέσιμη εκτίμηση, όχι βεβαιότητα.
"""
import csv
import datetime
import io
import json
import ssl
import urllib.request
from pathlib import Path

FOOTBALL_URLS = [
    "https://www.football-data.co.uk/fixtures.csv",
    "https://www.football-data.co.uk/new_league_fixtures.csv",
]
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/{lg}/scoreboard?dates={d}"
ESPN_LEAGUES = {"nba": "NBA", "mens-college-basketball": "NCAA (ΗΠΑ)", "wnba": "WNBA"}

LEAGUES = {
    "E0": "Αγγλία – Premier League", "E1": "Αγγλία – Championship", "E2": "Αγγλία – League One",
    "E3": "Αγγλία – League Two", "EC": "Αγγλία – National League", "SC0": "Σκωτία – Premiership",
    "SC1": "Σκωτία – Championship", "SC2": "Σκωτία – League One", "SC3": "Σκωτία – League Two",
    "D1": "Γερμανία – Bundesliga", "D2": "Γερμανία – 2. Bundesliga", "I1": "Ιταλία – Serie A",
    "I2": "Ιταλία – Serie B", "SP1": "Ισπανία – La Liga", "SP2": "Ισπανία – Segunda",
    "F1": "Γαλλία – Ligue 1", "F2": "Γαλλία – Ligue 2", "N1": "Ολλανδία – Eredivisie",
    "B1": "Βέλγιο – Jupiler League", "P1": "Πορτογαλία – Liga Portugal", "T1": "Τουρκία – Super Lig",
    "G1": "Ελλάδα – Super League",
}

DATA_DIR = Path.home() / ".stoixima"
import os as _os  # noqa: E402
import threading as _threading  # noqa: E402

_IO = _threading.RLock()
DEFAULTS = {"football": True, "basketball": True, "hours": 72, "min_prob": 65, "slip": 3,
            "overunder": True}


def set_dir(path):
    global DATA_DIR
    DATA_DIR = Path(path)


def _read(name, default):
    try:
        with _IO:
            return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write(name, data):
    """Ασφαλής εγγραφή (προσωρινό αρχείο + αντικατάσταση, με κλείδωμα): τα δεδομένα δεν «χαλάνε» ποτέ."""
    with _IO:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DATA_DIR / (name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _os.replace(tmp, DATA_DIR / name)


def load_settings():
    return {**DEFAULTS, **_read("settings.json", {})}


def save_settings(s):
    _write("settings.json", s)


def load_cache():
    return _read("matches.json", {"matches": [], "updated": None, "notes": []})


# ------------------------------------------------------------------ λήψη
def _get(url, timeout=20):
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14) Stoixima/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def _f(x):
    try:
        v = float(str(x).strip())
        return v if v > 1.0 else None
    except (TypeError, ValueError):
        return None


def _uk_offset(d):
    """Ώρα Αγγλίας: +1 από την τελευταία Κυριακή Μαρτίου ως την τελευταία Κυριακή Οκτωβρίου."""
    def last_sunday(month):
        x = datetime.date(d.year, month, 31)
        return x - datetime.timedelta(days=(x.weekday() + 1) % 7)
    return 1 if last_sunday(3) <= d < last_sunday(10) else 0


def _utc_from_uk(date_s, time_s):
    d = None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            d = datetime.datetime.strptime(date_s.strip(), fmt)
            break
        except ValueError:
            continue
    if d is None:
        return None
    try:
        hh, mm = (time_s or "15:00").strip().split(":")[:2]
        d = d.replace(hour=int(hh), minute=int(mm))
    except ValueError:
        d = d.replace(hour=15)
    return (d - datetime.timedelta(hours=_uk_offset(d.date()))).replace(tzinfo=datetime.timezone.utc)


def _pick(row, *names):
    for n in names:
        v = _f(row.get(n))
        if v:
            return v
    return None


def parse_football_csv(raw):
    text = raw.decode("utf-8-sig", errors="replace")
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items() if isinstance(v, str) or v is None}
        home = row.get("HomeTeam") or row.get("Home")
        away = row.get("AwayTeam") or row.get("Away")
        if not home or not away:
            continue
        start = _utc_from_uk(row.get("Date", ""), row.get("Time", ""))
        if start is None:
            continue
        div = row.get("Div", "")
        league = LEAGUES.get(div) or " – ".join(x for x in (row.get("Country"), row.get("League")) if x) or div
        # Pinnacle (η πιο ακριβής αγορά), αλλιώς μέσος όρος αγοράς
        h = _pick(row, "PSH", "PH", "AvgH", "BbAvH", "B365H")
        dr = _pick(row, "PSD", "PD", "AvgD", "BbAvD", "B365D")
        a = _pick(row, "PSA", "PA", "AvgA", "BbAvA", "B365A")
        if not (h and dr and a):
            continue
        pin = bool(_pick(row, "PSH", "PH"))
        country = row.get("Country", "")
        m = {"sport": "football", "league": league, "home": home, "away": away,
             "start": start.isoformat(), "x12": [h, dr, a], "div": div, "country": country, "pin": pin,
             "margin": round(1 / h + 1 / dr + 1 / a - 1, 4),
             "max": [_pick(row, "MaxH", "BbMxH") or h, _pick(row, "MaxD", "BbMxD") or dr,
                     _pick(row, "MaxA", "BbMxA") or a]}
        o = _pick(row, "P>2.5", "Avg>2.5", "BbAv>2.5", "B365>2.5")
        u = _pick(row, "P<2.5", "Avg<2.5", "BbAv<2.5", "B365<2.5")
        if o and u:
            m["ou"] = [o, u]
        out.append(m)
    return out


def _american(x):
    try:
        v = float(str(x).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if v == 0:
        return None
    return 1 + v / 100 if v > 0 else 1 + 100 / abs(v)


def _ml(team_odds):
    if not isinstance(team_odds, dict):
        return None
    for k in ("moneyLine", "moneyline"):
        if k in team_odds:
            v = team_odds[k]
            if isinstance(v, dict):
                v = (v.get("close") or v.get("open") or {}).get("odds")
            return _american(v)
    return None


def parse_espn(js, league_name):
    out = []
    for ev in js.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        state = ((comp.get("status") or ev.get("status") or {}).get("type") or {}).get("state")
        if state not in (None, "pre"):
            continue
        teams = {c.get("homeAway"): (c.get("team") or {}).get("displayName") for c in comp.get("competitors", [])}
        odds = (comp.get("odds") or [None])[0] or {}
        h = _ml(odds.get("homeTeamOdds"))
        a = _ml(odds.get("awayTeamOdds"))
        ml = odds.get("moneyline") or {}
        if not (h and a) and isinstance(ml, dict):
            def g(side):
                s = ml.get(side) or {}
                return _american((s.get("close") or s.get("open") or {}).get("odds"))
            h, a = g("home"), g("away")
        if not (h and a and teams.get("home") and teams.get("away")):
            continue
        out.append({"sport": "basketball", "league": league_name, "home": teams["home"], "away": teams["away"],
                    "start": ev.get("date", ""), "x12": [h, None, a], "max": [h, None, a], "pin": False,
                    "margin": round(1 / h + 1 / a - 1, 4), "espn_id": ev.get("id", ""),
                    "info": odds.get("details") or ""})
    return out


# ------------------------------------------------------------------ 2η πηγή ποδοσφαίρου: ESPN
ESPN_SOCCER = "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/scoreboard?dates={d}"
ESPN_SOCCER_LEAGUES = {
    # ευρωπαϊκές διοργανώσεις & εθνικές ομάδες
    "uefa.champions": "UEFA Champions League", "uefa.europa": "UEFA Europa League",
    "uefa.europa.conf": "UEFA Conference League", "uefa.nations": "UEFA Nations League",
    "fifa.worldq.uefa": "Προκριματικά Μουντιάλ (Ευρώπη)", "uefa.euroq": "Προκριματικά Euro",
    "fifa.friendly": "Φιλικά εθνικών",
    # Ελλάδα, Κύπρος
    "gre.1": "Ελλάδα – Super League", "cyp.1": "Κύπρος – Α' Κατηγορία",
    # μεγάλα πρωταθλήματα (και ως 2η πηγή για όσα έχει το football-data)
    "eng.1": "Αγγλία – Premier League", "eng.2": "Αγγλία – Championship", "eng.fa": "Αγγλία – Κύπελλο FA",
    "eng.league_cup": "Αγγλία – League Cup", "esp.1": "Ισπανία – La Liga", "esp.2": "Ισπανία – La Liga 2",
    "esp.copa_del_rey": "Ισπανία – Κύπελλο", "ita.1": "Ιταλία – Serie A", "ita.2": "Ιταλία – Serie B",
    "ita.coppa_italia": "Ιταλία – Κύπελλο", "ger.1": "Γερμανία – Bundesliga", "ger.2": "Γερμανία – 2. Bundesliga",
    "ger.dfb_pokal": "Γερμανία – Κύπελλο", "fra.1": "Γαλλία – Ligue 1", "fra.2": "Γαλλία – Ligue 2",
    "ned.1": "Ολλανδία – Eredivisie", "por.1": "Πορτογαλία – Primeira Liga", "bel.1": "Βέλγιο – Pro League",
    "tur.1": "Τουρκία – Süper Lig", "sco.1": "Σκωτία – Premiership", "aut.1": "Αυστρία – Bundesliga",
    "sui.1": "Ελβετία – Super League", "den.1": "Δανία – Superliga", "nor.1": "Νορβηγία – Eliteserien",
    "swe.1": "Σουηδία – Allsvenskan",
    # Αμερική
    "usa.1": "ΗΠΑ – MLS", "bra.1": "Βραζιλία – Serie A", "arg.1": "Αργεντινή – Primera División",
    "mex.1": "Μεξικό – Liga MX", "conmebol.libertadores": "Copa Libertadores",
}
ESPN_TIER = {k: 1 for k in ("uefa.champions", "uefa.europa", "eng.1", "esp.1", "ita.1", "ger.1", "fra.1",
                            "uefa.nations", "fifa.worldq.uefa", "uefa.euroq")}
ESPN_TIER.update({k: 2 for k in ("uefa.europa.conf", "gre.1", "eng.2", "ned.1", "por.1", "bel.1", "tur.1",
                                 "sco.1", "ger.2", "ita.2", "esp.2", "fra.2", "usa.1", "bra.1", "arg.1",
                                 "eng.fa", "esp.copa_del_rey", "ita.coppa_italia", "ger.dfb_pokal", "aut.1",
                                 "sui.1", "den.1", "mex.1", "conmebol.libertadores")})


def parse_espn_soccer(js, league_name, slug=""):
    out = []
    for ev in js.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        state = ((comp.get("status") or ev.get("status") or {}).get("type") or {}).get("state")
        if state not in (None, "pre"):
            continue
        teams = {c.get("homeAway"): (c.get("team") or {}).get("displayName") for c in comp.get("competitors", [])}
        odds = (comp.get("odds") or [None])[0] or {}
        h = _ml(odds.get("homeTeamOdds"))
        a = _ml(odds.get("awayTeamOdds"))
        d = _ml(odds.get("drawOdds"))
        ml = odds.get("moneyline") or {}
        if not (h and a and d) and isinstance(ml, dict):
            def g(side):
                s_ = ml.get(side) or {}
                return _american((s_.get("close") or s_.get("open") or {}).get("odds"))
            h, a, d = g("home"), g("away"), g("draw")
        if not (h and a and d and teams.get("home") and teams.get("away")):
            continue
        m = {"sport": "football", "league": league_name, "home": teams["home"], "away": teams["away"],
             "start": ev.get("date", ""), "x12": [h, d, a], "max": [h, d, a], "div": "", "country": "",
             "pin": False, "margin": round(1 / h + 1 / d + 1 / a - 1, 4), "src": "ESPN",
             "uefa": league_name.startswith("UEFA"), "espn": slug, "tier": ESPN_TIER.get(slug, 3)}
        if league_name.startswith("Ελλάδα"):
            m["country"] = "Greece"
        out.append(m)
    return out


def _norm_team(name):
    import unicodedata
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    for w in (" fc", "fc ", " afc", " cf", " sc", " fk", "ac ", " ac", "as ", "ss ", " calcio", " piraeus",
              " athens", "1. ", " cp"):
        n = n.replace(w, " ")
    return set(t for t in "".join(ch if ch.isalnum() else " " for ch in n).split() if len(t) > 2)


def _same_match(a, b):
    if a["start"][:10] != b["start"][:10] and abs((_start(a) - _start(b)).total_seconds()) > 36 * 3600:
        return False
    return _team_like(a["home"], b["home"]) and _team_like(a["away"], b["away"])


ALIASES = {"man": "manchester", "utd": "united", "wolves": "wolverhampton", "spurs": "tottenham",
           "nottm": "nottingham", "sheff": "sheffield", "olympiakos": "olympiacos", "internazionale": "inter",
           "bayern": "bayern", "psg": "paris", "athletico": "atletico", "atl": "atletico"}
STOP = {"real", "city", "united", "athletic", "sporting", "club", "inter", "dynamo", "olympique", "racing",
        "deportivo", "atletico", "town", "rovers", "wanderers", "borussia", "union", "royal", "saint", "san",
        "county", "albion", "hotspur", "forest"}


def _team_like(x, y):
    """Ίδια ομάδα με διαφορετική γραφή (π.χ. Olympiakos / Olympiacos Piraeus, Man United / Manchester United);"""
    import difflib
    tx = {ALIASES.get(t, t) for t in _norm_team(x)}
    ty = {ALIASES.get(t, t) for t in _norm_team(y)}
    nx, ny = tx - STOP, ty - STOP
    sx, sy = tx & STOP, ty & STOP

    def close(a, b):
        return a == b or (len(a) >= 5 and len(b) >= 5 and (a.startswith(b) or b.startswith(a)
                                                         or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8))
    if nx and ny:
        if not any(close(a, b) for a in nx for b in ny):
            return False
        if sx and sy and not (sx & sy):
            return False          # π.χ. Manchester United ≠ Manchester City
        return True
    return bool(tx & ty)


def fetch_all(settings, log=lambda s: None):
    """Όλες οι πηγές παράλληλα, με όριο χρόνου ανά πηγή. Οι αγώνες που υπάρχουν σε δύο πηγές ενώνονται
    και κρατούν και τις δύο εκτιμήσεις (συμφωνία πηγών = μεγαλύτερη αξιοπιστία)."""
    from concurrent.futures import ThreadPoolExecutor
    jobs = []
    today = datetime.date.today()
    days = max(1, int(settings["hours"]) // 24 + 1)
    if settings["football"]:
        for url in FOOTBALL_URLS:
            jobs.append(("fd", url, None))
        for lg, name in ESPN_SOCCER_LEAGUES.items():
            for i in range(days):
                jobs.append(("es", lg, (name, (today + datetime.timedelta(days=i)).strftime("%Y%m%d"))))
    if settings["basketball"]:
        for lg, name in ESPN_LEAGUES.items():
            for i in range(days):
                jobs.append(("eb", lg, (name, (today + datetime.timedelta(days=i)).strftime("%Y%m%d"))))

    def run(job):
        kind, a, b = job
        if kind == "fd":
            return kind, parse_football_csv(_get(a, timeout=20))
        if kind == "es":
            return kind, parse_espn_soccer(json.loads(_get(ESPN_SOCCER.format(lg=a, d=b[1]), timeout=12)), b[0], a)
        return kind, parse_espn(json.loads(_get(ESPN.format(lg=a, d=b[1]), timeout=12)), b[0])

    fd, es, bb, notes = [], [], [], []
    log(f"Λήψη από {len(jobs)} πηγές…")
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(run, j) for j in jobs]
        for j, f in zip(jobs, futs):
            try:
                kind, res = f.result(timeout=40)
                (fd if kind == "fd" else es if kind == "es" else bb).extend(res)
            except Exception as e:  # noqa: BLE001
                if j[0] == "fd":
                    notes.append(f"football-data: {str(e)[:60]}")
    # ένωση: ESPN πάνω στα football-data (δεύτερη γνώμη) ή ως νέος αγώνας (π.χ. UEFA)
    matches = list(fd)
    for e in es:
        twin = next((m for m in fd if _same_match(m, e)), None)
        if twin is not None:
            twin["alt"] = e["x12"]
            twin["sources"] = ["football-data", "ESPN"]
        elif not any(_same_match(m, e) for m in matches):
            e["sources"] = ["ESPN"]
            matches.append(e)
    for m in fd:
        m.setdefault("sources", ["football-data"])
    for m in bb:
        m.setdefault("sources", ["ESPN"])
    matches += bb
    seen, uniq = set(), []
    for m in matches:
        k = (m["home"], m["away"], m["start"][:10])
        if k not in seen:
            seen.add(k)
            uniq.append(m)
    if not uniq and notes:
        raise RuntimeError(notes[0])
    data = {"matches": uniq, "updated": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), "notes": notes}
    _write("matches.json", data)
    log(f"{len(uniq)} αγώνες")
    return data


def consensus(m, sign):
    """Πιθανότητα του σημείου από τη 2η πηγή (αν υπάρχει)."""
    alt = m.get("alt")
    if not alt or sign not in ("1", "Χ", "2", "1Χ", "Χ2", "12"):
        return None
    f = _fair(alt)
    one = {"1": f[0], "Χ": f[1], "2": f[2]}
    one.update({"1Χ": f[0] + f[1], "Χ2": f[1] + f[2], "12": f[0] + f[2]})
    return one[sign]


# ------------------------------------------------------------------ ανάλυση
GR_DAYS = ["Δευ", "Τρι", "Τετ", "Πεμ", "Παρ", "Σαβ", "Κυρ"]


def _start(m):
    try:
        return datetime.datetime.fromisoformat(m["start"].replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def local_time(m):
    t = _start(m)
    if not t:
        return ""
    t = t.astimezone()
    return f"{GR_DAYS[t.weekday()]} {t:%d/%m %H:%M}"


def _fair(odds):
    inv = [1 / o for o in odds]
    tot = sum(inv)
    return [x / tot for x in inv]


def picks_for(m, overunder=True):
    out = []
    o = m["x12"]
    if m["sport"] == "football":
        fair = _fair(o)
        labels = [("1", f"Νίκη {m['home']}"), ("Χ", "Ισοπαλία"), ("2", f"Νίκη {m['away']}")]
        for i, (sign, name) in enumerate(labels):
            out.append({"sign": sign, "name": name, "prob": fair[i], "fair_odds": 1 / fair[i],
                        "best": m["max"][i] or o[i]})
        for sign, idx, name in (("1Χ", (0, 1), f"{m['home']} ή ισοπαλία"),
                                ("Χ2", (1, 2), f"{m['away']} ή ισοπαλία"),
                                ("12", (0, 2), "Όχι ισοπαλία")):
            p = fair[idx[0]] + fair[idx[1]]
            out.append({"sign": sign, "name": name, "prob": p, "fair_odds": 1 / p, "best": None, "double": True})
        if overunder and m.get("ou"):
            f2 = _fair(m["ou"])
            out.append({"sign": "Over 2,5", "name": "3 ή περισσότερα γκολ", "prob": f2[0],
                        "fair_odds": 1 / f2[0], "best": m["ou"][0]})
            out.append({"sign": "Under 2,5", "name": "έως 2 γκολ", "prob": f2[1],
                        "fair_odds": 1 / f2[1], "best": m["ou"][1]})
    else:
        fair = _fair([o[0], o[2]])
        out.append({"sign": "1", "name": f"Νίκη {m['home']}", "prob": fair[0], "fair_odds": 1 / fair[0],
                    "best": o[0]})
        out.append({"sign": "2", "name": f"Νίκη {m['away']}", "prob": fair[1], "fair_odds": 1 / fair[1],
                    "best": o[2]})
    for p in out:
        p["ev"] = p["prob"] * p["best"] - 1 if p.get("best") else None
    return out


def upcoming(cache, settings):
    now = datetime.datetime.now(datetime.timezone.utc)
    limit = now + datetime.timedelta(hours=int(settings["hours"]))
    out = []
    for m in cache.get("matches", []):
        if m["sport"] == "football" and not settings["football"]:
            continue
        if m["sport"] == "basketball" and not settings["basketball"]:
            continue
        t = _start(m)
        if t and now + datetime.timedelta(minutes=5) <= t <= limit:
            out.append(m)
    out.sort(key=lambda m: m["start"])
    return out


def standards(matches, settings, limit=80):
    """Το πιο πιθανό απλό σημείο κάθε αγώνα (1/Χ/2/Over/Under), αν ξεπερνά το όριο πιθανότητας.
    Αν κανένα απλό σημείο δεν φτάνει το όριο, προτείνεται διπλή ευκαιρία."""
    res = []
    min_p = float(settings["min_prob"]) / 100
    for m in matches:
        ps = picks_for(m, settings["overunder"])
        simple = [p for p in ps if not p.get("double") and p["prob"] >= min_p]
        double = [p for p in ps if p.get("double") and p["prob"] >= min_p]
        best = max(simple, key=lambda p: p["prob"]) if simple else (
            max(double, key=lambda p: p["prob"]) if double else None)
        if best:
            res.append((m, best))
    res.sort(key=lambda mp: (mp[1].get("double", False), -mp[1]["prob"]))
    return res[:limit]


def build_slip(stds, size):
    """Δελτίο από τα πιο πιθανά απλά σημεία διαφορετικών αγώνων."""
    pool = [x for x in stds if not x[1].get("double")] or stds
    pool = sorted(pool, key=lambda mp: -mp[1]["prob"])
    chosen = pool[: max(1, int(size))]
    prob, fair_odds = 1.0, 1.0
    for _, p in chosen:
        prob *= p["prob"]
        fair_odds *= p["fair_odds"]
    return chosen, prob, fair_odds


def value_picks(matches, settings, min_ev=0.02, limit=60):
    res = []
    for m in matches:
        for p in picks_for(m, settings["overunder"]):
            if p["ev"] is not None and p["ev"] >= min_ev and p["prob"] >= 0.2:
                res.append((m, p))
    res.sort(key=lambda mp: -mp[1]["ev"])
    return res[:limit]


# ------------------------------------------------------------------ αποδόσεις ΟΠΑΠ (καταχώριση χρήστη)
PAME_URL = "https://www.pamestoixima.gr/"


def match_key(m):
    return f"{m['home']}|{m['away']}|{m['start'][:10]}"


def opap_all():
    return _read("opap_odds.json", {})


def opap_get(m):
    return opap_all().get(match_key(m), {})


def opap_set(m, sign, odds):
    d = opap_all()
    e = d.setdefault(match_key(m), {})
    if odds:
        e[sign] = round(float(odds), 2)
    else:
        e.pop(sign, None)
    # καθάρισμα παλιών (πάνω από 10 ημέρες)
    cutoff = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    d = {k: v for k, v in d.items() if k.split("|")[-1] >= cutoff}
    _write("opap_odds.json", d)


def opap_value(prob, odds):
    """Αναμενόμενη απόδοση ανά 1 € (π.χ. +0.05 = +5%)."""
    return prob * odds - 1
