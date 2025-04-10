import os
import asyncpg
from datetime import datetime, timezone, timedelta
from websockets.server import serve
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import json

app = FastAPI()
ACTIVE_DURATION = timedelta(seconds=30)

# Подключение к PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL")

# HTML для отображения данных (сохранен как static/index.html)
app.mount("/static", StaticFiles(directory="static"), name="static")

async def setup_database():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS user_data (
        deviceid TEXT PRIMARY KEY,
        ip TEXT NOT NULL,
        server TEXT NOT NULL,
        nickname TEXT NOT NULL,
        real_nickname TEXT DEFAULT 'None',
        license_active BOOLEAN NOT NULL,
        last_active TIMESTAMP NOT NULL,
        allowed BOOLEAN DEFAULT FALSE,
        unique_identifier TEXT UNIQUE
    );
    """)
    await conn.close()

@app.on_event("startup")
async def startup():
    await setup_database()

async def handle_websocket(websocket):
    conn = await asyncpg.connect(DATABASE_URL)
    current_time = datetime.now(timezone.utc)
    
    try:
        async for message in websocket:
            data = json.loads(message)
            
            # Обработка данных
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
            ON CONFLICT (unique_identifier)
            DO UPDATE SET
                ip = $2,
                server = $3,
                nickname = $4,
                license_active = $5,
                last_active = $6;
            """, deviceid, ip, server, nickname, license_active, current_time, unique_identifier)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        # При закрытии соединения обновляем last_active
        await conn.execute("""
        UPDATE user_data 
        SET last_active = $1 
        WHERE unique_identifier = $2;
        """, datetime.now(timezone.utc), unique_identifier)
        await conn.close()

@app.get("/data")
async def get_data():
    conn = await asyncpg.connect(DATABASE_URL)
    current_time = datetime.now(timezone.utc)
    
    rows = await conn.fetch("""
    SELECT nickname, real_nickname, server, license_active, last_active, allowed
    FROM user_data
    ORDER BY
        (current_timestamp - last_active) <= INTERVAL '30 seconds' DESC,
        last_active DESC;
    """)
    
    response = []
    for row in rows:
        time_diff = current_time - row['last_active']
        
        if time_diff < ACTIVE_DURATION:
            active = True
            last_active_str = "Online"
        else:
            active = False
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
    # Реализуйте форматирование времени как в оригинале
    pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)