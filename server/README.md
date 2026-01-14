# Drum AR HUD - Server (Stage 2)

Servidor mínimo para:
- carregar um projeto JSON (ex.: `examples/seven_nation_army.json`)
- expor o projeto via HTTP
- expor o estado de transporte via HTTP + WebSocket

## Rodar (Windows / Linux / macOS)

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r server/requirements.txt
python server/server.py --project examples/seven_nation_army.json
```

## Endpoints

- `GET  /api/health` → `{ "ok": true }`
- `GET  /api/project` → retorna o JSON do projeto
- `GET  /api/state` → retorna estado atual (playing/bar/beat/bpm/ppq...)
- `POST /api/state` → atualiza estado e transmite para os clientes WS

Exemplo:
```bash
curl -X POST http://localhost:8765/api/state \
  -H "Content-Type: application/json" \
  -d "{\"playing\":true,\"bar\":1,\"beat\":1,\"bpm\":124,\"ppq\":0}"
```

WebSocket:
- `ws://localhost:8765/ws/state` (envia estado inicial + updates)
