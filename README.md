# GPU-Preisalarm Black Week 2026

Überwacht 4 Radeon RX 9070 XT (Red Devil, Taichi, Mercury, Nitro+) und 2 Monitore (Alienware AW2725D, ASUS XG27AQDMES) auf Geizhals.de und schickt Push-Nachrichten (ntfy) bei Zielpreis, Preissturz (≥ 8 % unter 7-Tage-Median), neuem Tiefstpreis oder wenn eine Karte wieder lieferbar ist.

## Schnellstart (lokal)

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Linux: .venv/bin/pip
python main.py --test-notify      # Test-Push an das Topic aus .env
python main.py --once --dry-run   # ein Durchlauf, Pushes nur ins Log
python main.py                    # Dauerbetrieb bis poll.run_until
python report.py                  # Tabelle: aktueller Stand, Median, Tiefstpreis
python -m pytest -q               # Tests (Parser gegen gespeicherte Geizhals-Seiten, Regeln, Spam-Schutz)
```

## Handy einrichten
ntfy-App installieren (iOS/Android) → „Subscribe to topic“ → Topic aus `.env` (`NTFY_TOPIC`) eintragen, Server `ntfy.sh`.

## Betrieb über GitHub Actions
Der Workflow `.github/workflows/poll.yml` läuft alle 30 min, vom 26.–30.11. alle 15 min, ruft `python main.py --once` auf und committet `data/prices.sqlite3` zurück ins Repo (Preisverlauf).
- ntfy-Topic liegt als Repository-Secret `NTFY_TOPIC` (optional `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
- Manuell starten: Actions → GPU-Preisalarm → Run workflow (oder `gh workflow run poll.yml`).
- Config ändern (z. B. Zielpreise): `config.yaml` bearbeiten, committen, pushen – vorher `git pull`, weil der Bot laufend Preisdaten committet.
- Nach dem 01.12. macht jeder Lauf nichts mehr; Workflow dann unter Actions deaktivieren.
- GitHub startet geplante Läufe oft 5–15 min verspätet.

Alternativ auf einem eigenen Linux-Rechner: `deploy/gpu-price-alert.service` (systemd) oder Docker:
`docker build -t gpu-alert . && docker run -d --restart unless-stopped --env-file .env -v $PWD/data:/app/data gpu-alert`

## Übersichtsseite
https://filipstanicak.github.io/gpu-price-alert/ – Bestpreis, Ziel, Abstand und Preisverlauf je Karte.
Wird nach jedem Lauf vom Workflow neu gebaut (`python dashboard.py --out site`) und über GitHub Pages veröffentlicht.
Der Tagesreport-Push öffnet die Seite beim Antippen.

## Wichtige Details
- **Nur Geizhals-Produktseiten** werden abgerufen (laut robots.txt erlaubt); Suche (`/?fs=`) und `/redir/` werden nie automatisch aufgerufen. Mindestens 4 s Pause zwischen Requests, Intervall nie unter 10 min.
- **Varianten-Schutz:** Jede Seite muss die MPN aus `mpns` exakt tragen (z. B. `RX9070XT 16G-E/OC/WHITE` wird abgelehnt) und der Seitentitel darf kein `exclude_keywords`-Wort enthalten. Sonst → einmalige Warn-Push.
- **Direkte Shop-Abfrage:** Mindfactory, Alternate und Proshop werden zusätzlich direkt abgefragt (`shop_urls`, schema.org-Produktdaten). Deren Preis ersetzt Geizhals' (oft 30–60 min alte) Kopie desselben Shops. idealo, Notebooksbilliger, Cyberport, Computeruniverse, Galaxus und Amazon blockieren automatische Abrufe und werden nur über Geizhals erfasst.
- **Verglichen wird nur der Kartenpreis** (`include_shipping: false`), Versandkosten werden ignoriert. Mit `true` wird stattdessen Artikel + Standardversand verglichen.
- **Lieferbar** = Geizhals-Status „lagernd“ oder Lieferzeit ≤ `max_delivery_days`.
- `target_price: 0` = Zielpreis-Regel aus. Nach 2–3 Tagen Verlauf (`python report.py`) setzen.
- Akzeptanztest Zielpreis: `target_price` testweise hoch setzen und mit separater DB laufen lassen, damit der Test-Alarm echte Alarme nicht blockiert:
  `python main.py --once --db data/test.sqlite3`
