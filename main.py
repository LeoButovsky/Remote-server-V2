import os
import json
import asyncio
from aiohttp import web, WSMsgType
import asyncpg
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL")

connected_clients = set()
active_frontend_clients = set()
active_deviceids = set()

async def setup_database(pool):
    async with pool.acquire() as conn:
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

async def notify_clients():
    for ws in active_frontend_clients.copy():
        if not ws.closed:
            try:
                await ws.send_str("update")
            except Exception as e:
                print(f"Notify error: {e}")
                active_frontend_clients.discard(ws)

async def handle_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    deviceid = None
    connected_clients.add(ws)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    deviceid = data['deviceid']
                    nickname = data['nickname']
                    server = data['server']
                    game_state = data['game_state']
                    ip = request.remote
                    current_time = datetime.now()

                    active_deviceids.add(deviceid)

                    pool = request.app['pool']
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
                                await ws.send_str("Success!")
                            else:
                                await ws.send_str("Failed to check user")
                        else:
                            await conn.execute('''
                                INSERT INTO user_data 
                                (deviceid, nickname, server, game_state, last_active, ip, expire_date)
                                VALUES ($1, $2, $3, $4, $5, $6, $7)
                            ''', deviceid, nickname, server, game_state, current_time, ip, None)
                            await ws.send_str("Failed to check user")

                    await notify_clients()

                except Exception as e:
                    print(f"WS error: {e}")
                    await ws.send_str("Failed to check user")

            elif msg.type == WSMsgType.ERROR:
                print(f'ws connection closed with exception {ws.exception()}')

    finally:
        connected_clients.discard(ws)
        if deviceid:
            active_deviceids.discard(deviceid)

    return ws

async def handle_frontend_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    active_frontend_clients.add(ws)

    try:
        async for _ in ws:
            pass
    finally:
        active_frontend_clients.discard(ws)

    return ws

async def get_users(request):
    pool = request.app['pool']
    async with pool.acquire() as conn:
        users = await conn.fetch('''
            SELECT deviceid, real_nickname, nickname, server, game_state, 
                   last_active, allowed, expire_date 
            FROM user_data
        ''')

        users_data = []
        current_time = datetime.now()
        for user in users:
            delta = current_time - user['last_active']
            is_active = user['deviceid'] in active_deviceids

            active_status = "Now" if is_active else \
                f"{delta.days}d {delta.seconds // 3600}h {(delta.seconds // 60) % 60}m ago"

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

async def on_shutdown(app):
    await app['pool'].close()

async def main():
    # Создаем пул соединений
    pool = await asyncpg.create_pool(DATABASE_URL)
    # Инициализируем базу данных
    await setup_database(pool)

    app = web.Application()
    app['pool'] = pool
    app.on_shutdown.append(on_shutdown)
    
    app.router.add_get('/', index)
    app.router.add_get('/get_users', get_users)
    app.router.add_get('/ws', handle_ws)
    app.router.add_get('/frontend_ws', handle_frontend_ws)

    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    print(f"Server running on port {port}")
    await site.start()
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
