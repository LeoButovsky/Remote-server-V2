import os
import asyncpg
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import json
from websockets.server import WebSocketServerProtocol as WebSocket

app = FastAPI()
ACTIVE_DURATION = timedelta(seconds=30)
DATABASE_URL = os.getenv("DATABASE_URL")

# HTML-шаблон встроен прямо в код
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>User Data</title>
    <style>
        /* Ваши стили из оригинального кюда */
    </style>
</head>
<body>
    <div class="container">
        <!-- Ваша HTML-структура -->
    </div>
    <script>
        // JavaScript из оригинального кода
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(content=HTML_TEMPLATE)

# WebSocket обработчик
async def ws_handler(websocket: WebSocket):
    await websocket.accept()
    conn = await asyncpg.connect(DATABASE_URL)
    current_time = datetime.now(timezone.utc)
    
    try:
        async for message in websocket:
            data = json.loads(message)
            
            deviceid = data.get("deviceid", "-")
            ip = websocket.remote_address[0]
            server = data.get("server", "unknown")
            nickname = data.get("nickname", "unknown")
            license_active = data.get("gamestate", 0) == 1
            unique_identifier = deviceid if deviceid != '-' else ip

            await conn.execute("""
            INSERT INTO user_data 
            (deviceid, ip, server, nickname, license_active, last_active, unique_identifier)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (unique_identifier) DO UPDATE SET
                ip = $2,
                server = $3,
                nickname = $4,
                license_active = $5,
                last_active = $6;
            """, deviceid, ip, server, nickname, license_active, current_time, unique_identifier)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        await conn.execute("""
        UPDATE user_data 
        SET last_active = $1 
        WHERE unique_identifier = $2;
        """, datetime.now(timezone.utc), unique_identifier)
        await conn.close()

# Эндпоинт для данных
@app.get("/data")
async def get_data():
    conn = await asyncpg.connect(DATABASE_URL)
    current_time = datetime.now(timezone.utc)
    
    rows = await conn.fetch("""
    SELECT nickname, real_nickname, server, license_active, last_active, allowed
    FROM user_data
    ORDER BY (current_timestamp - last_active) <= INTERVAL '30 seconds' DESC,
             last_active DESC;
    """)
    
    response = []
    for row in rows:
        time_diff = current_time - row['last_active']
        active = time_diff < ACTIVE_DURATION
        last_active_str = format_last_active(time_diff, row['last_active'])
        
        response.append({
            "nickname": row['nickname'],
            "real_nickname": row['real_nickname'],
            "server": row['server'],
            "license_active": row['license_active'],
            "last_active": last_active_str,
            "active": active,
            "allowed": row['allowed']
        })
    
    await conn.close()
    return response

def format_last_active(time_diff, last_active):
    # Реализация форматирования времени как в оригинале
    pass

# Запуск WebSocket сервера
import uvicorn
from websockets.server import serve
import asyncio

async def main():
    server = await serve(ws_handler, "0.0.0.0", 8080)
    await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
    uvicorn.run(app, host="0.0.0.0", port=8080)
