import websockets
import asyncpg
import json
from datetime import datetime
from aiohttp import web
import asyncio
import os

# Получаем переменные среды
DATABASE_URL = os.getenv('DATABASE_URL')
WS_HOST = os.getenv('WS_HOST', '0.0.0.0')
WS_PORT = int(os.getenv('WS_PORT', 8765))
FRONTEND_WS_PORT = int(os.getenv('FRONTEND_WS_PORT', 8766))
HTTP_HOST = os.getenv('HTTP_HOST', '0.0.0.0')
HTTP_PORT = int(os.getenv('PORT', 8080))  # Railway использует PORT для HTTP сервера

connected_clients = set()
active_frontend_clients = set()
active_deviceids = set()

async def setup_database():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS user_data (
            deviceid TEXT PRIMARY KEY,
            real_nickname TEXT DEFAULT 'None',
            nickname TEXT NOT NULL,
            server TEXT NOT NULL,
            game_state TEXT NOT NULL,
            last_active TIMESTAMP NOT NULL,
            allowed BOOLEAN DEFAULT FALSE,
            ip TEXT NOT NULL,
            expire_date DATE
        );
    ''')
    await conn.close()

async def notify_clients():
    for client in active_frontend_clients.copy():
        try:
            await client.send("update")
        except Exception as e:
            print(f"Ошибка отправки клиенту: {e}")
            active_frontend_clients.discard(client)

async def safe_send(websocket, message):
    if websocket.closed:
        return
    try:
        await websocket.send(message)
    except Exception as e:
        print(f"[safe_send] Ошибка отправки сообщения: {e}")

async def handle_websocket(websocket, path):
    deviceid = None
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            data = json.loads(message)
            deviceid = data['deviceid']
            nickname = data['nickname']
            server = data['server']
            game_state = data['game_state']
            ip = websocket.remote_address[0]
            current_time = datetime.now()

            active_deviceids.add(deviceid)

            async with asyncpg.create_pool(DATABASE_URL) as pool:
                async with pool.acquire() as conn:
                    user = await conn.fetchrow(
                        'SELECT * FROM user_data WHERE deviceid = $1', deviceid
                    )

                    if user:
                        await conn.execute('''
                            UPDATE user_data SET
                            nickname = $1,
                            server = $2,
                            game_state = $3,
                            last_active = $4,
                            ip = $5
                            WHERE deviceid = $6
                        ''', nickname, server, game_state, current_time, ip, deviceid)

                        if user['allowed'] and (
                                user['expire_date'] is None or user['expire_date'] >= current_time.date()):
                            response = "Success!"
                        else:
                            response = "Failed to check user"
                    else:
                        await conn.execute('''
                            INSERT INTO user_data 
                            (deviceid, nickname, server, game_state, last_active, ip, expire_date)
                            VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ''', deviceid, nickname, server, game_state, current_time, ip, None)
                        response = "Failed to check user"

            await safe_send(websocket, response)
            await notify_clients()
    except Exception as e:
        print(f"Ошибка: {e}")
        await safe_send(websocket, "Failed to check user")
    finally:
        connected_clients.discard(websocket)
        if deviceid:
            active_deviceids.discard(deviceid)

async def handle_frontend_websocket(websocket, path):
    active_frontend_clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        active_frontend_clients.discard(websocket)

async def get_users(request):
    async with asyncpg.create_pool(DATABASE_URL) as pool:
        async with pool.acquire() as conn:
            users = await conn.fetch('''
                SELECT deviceid, real_nickname, nickname, server, game_state, 
                       last_active, allowed, expire_date 
                FROM user_data
            ''')

            users_data = []
            current_time = datetime.now()
            for user in users:
                last_active = user['last_active']
                delta = current_time - last_active
                is_active = user['deviceid'] in active_deviceids

                active_status = "Now" if is_active else \
                    f"{delta.days} days {delta.seconds // 3600} hours {(delta.seconds // 60) % 60} minutes ago"

                expire_days = (user['expire_date'] - current_time.date()).days if user['expire_date'] else None

                users_data.append({
                    'real_nickname': user['real_nickname'],
                    'nickname': user['nickname'],
                    'server': user['server'],
                    'game_state': user['game_state'],
                    'last_active': active_status,
                    'allowed': user['allowed'],
                    'expire_days': expire_days
                })

            return web.json_response(users_data)

async def index(request):
    return web.FileResponse('./index.html')

async def main():
    await setup_database()

    # Запускаем WebSocket серверы
    ws_server = websockets.serve(
        handle_websocket, 
        WS_HOST, 
        WS_PORT
    )
    
    frontend_ws = websockets.serve(
        handle_frontend_websocket, 
        WS_HOST, 
        FRONTEND_WS_PORT
    )

    # Настраиваем HTTP сервер
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/get_users', get_users)
    app.router.add_static('/static/', path='./static', name='static')
    
    runner = web.AppRunner(app)
    await runner.setup()
    http_server = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)

    await asyncio.gather(ws_server, frontend_ws, http_server.start())
    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
