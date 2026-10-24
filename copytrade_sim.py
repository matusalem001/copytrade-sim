"""
Simulation de copy-trading Polymarket (sans argent réel).

À chaque exécution :
  1. lit les nouveaux trades publics des traders suivis (config.json)
  2. pour chaque premier achat d'un trader sur un résultat, "achète" une mise
     fictive au meilleur prix vendeur disponible MAINTENANT (le vrai prix qu'on
     aurait payé), si l'écart avec son prix reste sous la limite
  3. si le trader revend un résultat qu'on détient, on "revend" au meilleur
     prix acheteur disponible
  4. règle les positions des marchés terminés (1 $ par part gagnante, 0 sinon)
  5. réécrit RAPPORT.md

Aucune clé, aucun compte, aucun ordre réel : uniquement des lectures d'API publiques.
"""
import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
STATE = os.path.join(DATA, "state.json")
POSITIONS = os.path.join(DATA, "positions.json")
JOURNAL = os.path.join(DATA, "journal.csv")
REPORT = os.path.join(ROOT, "RAPPORT.md")

DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

JOURNAL_FIELDS = [
    "date", "trader", "action", "marche", "resultat", "prix_trader",
    "prix_simule", "ecart_centimes", "parts", "montant", "pnl", "raison",
]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def get_json(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "copytrade-sim/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"Échec {url}: {last}")


def load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)


def journal(row):
    new = not os.path.exists(JOURNAL)
    with open(JOURNAL, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in JOURNAL_FIELDS})


def best_prices(token_id):
    """Meilleur prix vendeur (ask) et acheteur (bid) du carnet d'ordres."""
    book = get_json(f"{CLOB_API}/book?token_id={token_id}")
    asks = [float(a["price"]) for a in book.get("asks", []) if float(a.get("size", 0)) > 0]
    bids = [float(b["price"]) for b in book.get("bids", []) if float(b.get("size", 0)) > 0]
    return (min(asks) if asks else None), (max(bids) if bids else None)


def market_for_token(token_id):
    for extra in ("", "&closed=true"):
        res = get_json(f"{GAMMA_API}/markets?clob_token_ids={token_id}{extra}")
        if res:
            return res[0]
    return None


def resolution(token_id):
    """Renvoie 1.0 / 0.0 si le marché est réglé, sinon None."""
    m = market_for_token(token_id)
    if not m or not m.get("closed"):
        return None
    try:
        ids = json.loads(m.get("clobTokenIds") or "[]")
        prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
        p = prices[ids.index(token_id)]
    except (ValueError, IndexError, TypeError):
        return None
    if p >= 0.99:
        return 1.0
    if p <= 0.01:
        return 0.0
    return None  # fermé mais pas encore réglé


def fetch_trades(wallet):
    q = urllib.parse.urlencode({"user": wallet, "limit": 200, "takerOnly": "false"})
    return get_json(f"{DATA_API}/trades?{q}")


def trade_key(t):
    return f'{t.get("transactionHash")}|{t.get("asset")}|{t.get("side")}|{t.get("size")}|{t.get("price")}'


def run():
    os.makedirs(DATA, exist_ok=True)
    cfg = load(os.path.join(ROOT, "config.json"), {})
    stake = float(cfg.get("mise_par_copie", 10))
    max_gap = float(cfg.get("ecart_max_centimes", 3)) / 100
    max_price = float(cfg.get("prix_max", 0.95))
    fee = float(cfg.get("frais_pourcent", 0)) / 100

    state = load(STATE, {"debut": now_iso(), "traders": {}})
    positions = load(POSITIONS, {"ouvertes": {}, "fermees": []})

    for name, wallet in cfg["traders"].items():
        wallet = wallet.lower()
        st = state["traders"].setdefault(name, {"seen": [], "initialise": False})
        try:
            trades = fetch_trades(wallet)
        except RuntimeError as e:
            print(e)
            continue
        trades.sort(key=lambda t: t.get("timestamp", 0))
        seen = set(st["seen"])

        if not st["initialise"]:
            # Premier passage : on mémorise l'historique sans le copier.
            st["seen"] = [trade_key(t) for t in trades][-1000:]
            st["initialise"] = True
            print(f"{name}: initialisé ({len(trades)} trades passés ignorés)")
            continue

        new = [t for t in trades if trade_key(t) not in seen]
        for t in new:
            seen.add(trade_key(t))
            token = str(t.get("asset"))
            side = t.get("side")
            pos_id = f"{name}|{token}"
            base = {
                "date": now_iso(), "trader": name,
                "marche": t.get("title") or t.get("slug"), "resultat": t.get("outcome"),
                "prix_trader": round(float(t.get("price", 0)), 4),
            }
            try:
                ask, bid = best_prices(token)
            except RuntimeError as e:
                journal({**base, "action": "IGNORE", "raison": "carnet indisponible"})
                continue

            if side == "BUY":
                if pos_id in positions["ouvertes"]:
                    continue  # on ne copie que le premier achat sur un résultat
                if ask is None:
                    journal({**base, "action": "IGNORE", "raison": "aucun vendeur"})
                    continue
                gap = ask - float(t.get("price", 0))
                if gap > max_gap:
                    journal({**base, "action": "IGNORE", "prix_simule": ask,
                             "ecart_centimes": round(gap * 100, 1), "raison": "prix a trop bougé"})
                    continue
                if ask > max_price:
                    journal({**base, "action": "IGNORE", "prix_simule": ask, "raison": "prix trop haut"})
                    continue
                shares = stake * (1 - fee) / ask
                positions["ouvertes"][pos_id] = {
                    **base, "token": token, "prix_simule": ask, "parts": round(shares, 4),
                    "montant": stake, "ouvert_le": now_iso(),
                }
                journal({**base, "action": "ACHAT", "prix_simule": ask,
                         "ecart_centimes": round(gap * 100, 1), "parts": round(shares, 4), "montant": stake})

            elif side == "SELL" and pos_id in positions["ouvertes"]:
                p = positions["ouvertes"].pop(pos_id)
                price = bid or 0.0
                proceeds = p["parts"] * price * (1 - fee)
                pnl = proceeds - p["montant"]
                positions["fermees"].append({**p, "sortie": price, "pnl": round(pnl, 4),
                                             "ferme_le": now_iso(), "type": "revente"})
                journal({**base, "action": "VENTE", "prix_simule": price, "parts": p["parts"],
                         "montant": round(proceeds, 2), "pnl": round(pnl, 2)})

        st["seen"] = list(seen)[-1000:]

    # Règlement des marchés terminés
    for pos_id in list(positions["ouvertes"]):
        p = positions["ouvertes"][pos_id]
        try:
            r = resolution(p["token"])
        except RuntimeError:
            continue
        if r is None:
            continue
        payout = p["parts"] * r
        pnl = payout - p["montant"]
        positions["ouvertes"].pop(pos_id)
        positions["fermees"].append({**p, "sortie": r, "pnl": round(pnl, 4),
                                     "ferme_le": now_iso(), "type": "gagné" if r else "perdu"})
        journal({"date": now_iso(), "trader": p["trader"], "action": "REGLEMENT",
                 "marche": p["marche"], "resultat": p["resultat"], "prix_simule": p["prix_simule"],
                 "parts": p["parts"], "montant": round(payout, 2), "pnl": round(pnl, 2),
                 "raison": "gagné" if r else "perdu"})

    save(STATE, state)
    save(POSITIONS, positions)
    write_report(cfg, state, positions)


def write_report(cfg, state, positions):
    skipped = {}
    gaps = {}
    if os.path.exists(JOURNAL):
        with open(JOURNAL, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["action"] == "IGNORE":
                    skipped[row["trader"]] = skipped.get(row["trader"], 0) + 1
                if row["action"] == "ACHAT" and row["ecart_centimes"]:
                    gaps.setdefault(row["trader"], []).append(float(row["ecart_centimes"]))

    lines = [
        "# Simulation copy-trading Polymarket",
        "",
        f"Démarrée le **{state['debut']}** · mise à jour **{now_iso()}** · "
        f"mise fictive **{cfg.get('mise_par_copie', 10)} $** par copie",
        "",
        "| Trader | Copiés | Ignorés | Terminés | Gagnés | Profit réalisé | Rendement | En cours | Écart moyen |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    tot_pnl = tot_inv = 0.0
    for name in cfg["traders"]:
        closed = [p for p in positions["fermees"] if p["trader"] == name]
        opened = [p for p in positions["ouvertes"].values() if p["trader"] == name]
        pnl = sum(p["pnl"] for p in closed)
        inv = sum(p["montant"] for p in closed)
        wins = sum(1 for p in closed if p["pnl"] > 0)
        g = gaps.get(name, [])
        tot_pnl += pnl
        tot_inv += inv
        lines.append(
            f"| {name} | {len(closed) + len(opened)} | {skipped.get(name, 0)} | {len(closed)} | "
            f"{wins} | {pnl:+.2f} $ | {(pnl / inv * 100) if inv else 0:+.1f} % | {len(opened)} | "
            f"{(sum(g) / len(g)) if g else 0:+.1f} ¢ |"
        )
    lines += [
        "",
        f"**Total réalisé : {tot_pnl:+.2f} $ sur {tot_inv:.0f} $ engagés "
        f"({(tot_pnl / tot_inv * 100) if tot_inv else 0:+.1f} %)**",
        "",
        "*Écart moyen* = combien de centimes plus cher que le trader on a payé. "
        "*Ignorés* = trades non copiés (prix trop bougé, trop haut, ou carnet vide).",
        "",
        "## Dernières positions terminées",
        "",
        "| Trader | Marché | Résultat | Prix payé | Sortie | Profit |",
        "|---|---|---|---|---|---|",
    ]
    for p in positions["fermees"][-25:][::-1]:
        lines.append(f"| {p['trader']} | {p['marche']} | {p['resultat']} | {p['prix_simule']:.2f} | "
                     f"{p['sortie']:.2f} | {p['pnl']:+.2f} $ |")
    lines += ["", "Journal complet : `data/journal.csv`", ""]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    run()
