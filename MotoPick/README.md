# MotoPick iCube — Web HMI

Applicazione web per la configurazione e il controllo del sistema di automazione **Pick & Place MotoPick iCube** basato su controller **MP3300iec RBT**.

## Stack

| Layer | Tecnologia |
|-------|-----------|
| Backend | Python 3 + Flask |
| Storage | JSON su disco (persistente, atomic-write) |
| Controller mock | REST endpoints che simulano gRPC MP3300iec |
| Frontend | Vanilla JS SPA, SVG canvas interattivo |
| Font | Exo 2 + JetBrains Mono (Google Fonts) |

## Avvio rapido

```bash
# 1 — Installa dipendenze
pip install flask

# 2 — Avvia il server
python3 main.py

# 3 — Apri il browser
open http://localhost:8080
```

Porta di default: **8080** (override con env var `MOTOPICK_PORT`).

## Struttura directory

```
MotoPick/
├── main.py              # Backend Flask + REST API
├── requirements.txt
├── json_data/
│   └── project.json     # Dati progetto persistenti
└── templates/
    └── index.html       # SPA frontend (103 KB)
```

## API REST

### Progetto
| Method | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/api/project` | Dati completi del progetto |
| PUT | `/api/project/name` | Rinomina progetto |
| PUT | `/api/project/layout` | Salva posizioni canvas |

### Robots / Grippers / Feeds / Supplies / Products
Tutte le risorse seguono il pattern CRUD standard:
- `GET /api/{resource}` — lista
- `POST /api/{resource}` — crea
- `PUT /api/{resource}/{id}` — aggiorna
- `DELETE /api/{resource}/{id}` — elimina

### Formati
- `GET /api/formats` — lista slot
- `GET /api/formats/{id}` — formato completo con tutte le sotto-sezioni
- `POST /api/formats` — crea (body: `{name, template_id?}`)
- `DELETE /api/formats/{id}` — elimina
- `PUT /api/formats/{id}/{section}` — aggiorna sezione (grip_rules, work_areas, load_share, pick_patterns, place_patterns, item_sources, item_order, robot_motion, multi_pick, multi_place)

### Control (mock controller)
| Method | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/api/control/status` | Stato connessione |
| POST | `/api/control/connect` | Connetti al controller |
| POST | `/api/control/disconnect` | Disconnetti |
| POST | `/api/control/transmit` | Trasmetti progetto al controller |
| POST | `/api/control/load` | Carica formato `{format_id}` |
| POST | `/api/control/launch` | Avvia sistema |
| POST | `/api/control/stop` | Ferma sistema |

### Event Log
- `GET /api/events?limit=200&level=INFO` — leggi eventi
- `POST /api/events/clear` — cancella log

## Sezioni UI

### Tab Project
- **Layout** — Canvas SVG drag-drop con robot, nastri, camera, pattern host. Zoom con scroll, pan con click+drag su sfondo.
- **Robots** — IP, gripper, controller generation, feed slots, supply slots
- **Grippers** — 16 TCP con mode/tool/group, verify sensor
- **Feeds** — Tipo nastro, trigger generation, offsets per robot
- **Supplies** — Camera IP, vision driver (Cognex/Keyence/Custom), master robots
- **Products** — Nome, colore, tolerance X/Y, minimum score, TCP supportati
- **Formats** — Griglia 150 slot, create/delete, selezione attiva

### Tab Format
- **Multi Pick / Multi Place** — Abilita e modalità (Async/Sync)
- **Grip Rules** — 64 transizioni a toggle, tools con tipo/peso/livello
- **Work Areas** — Min/Max X,Y + slow/stop threshold per robot×nastro
- **Load Share** — Categorie tipo prodotto, bilanciamento, ratio robot
- **Pick/Place Patterns** — Items con posizione 6DOF, layer, tipi, peso
- **Item Sources** — Mapping feed→supply
- **Item Order** — Selezione item, ordinamento pick/place per robot
- **Robot Motion** — Approach/Processing/Escape: offset, velocità, precisione, duration, advance

### Tab Control
- **Cockpit** — Connect/Disconnect, Transmit, Load format, Launch/Stop, Event Log in tempo reale
- **Adjust Motion** — (richiede connessione attiva al controller)

## Prossimi passi per produzione

1. **Sostituire il mock controller** con client gRPC reale verso MP3300iec RBT
2. **Aggiungere autenticazione** (login base o token)
3. **WebSocket** per event log in streaming real-time
4. **Validazione** lato server dei dati di configurazione
5. **Esportazione/importazione** progetto come file ZIP
6. **Produzione WSGI**: `gunicorn -w 2 main:app`
