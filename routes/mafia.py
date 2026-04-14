from aiohttp import web, WSMsgType
import json
import uuid
import asyncio
import logging
from typing import Dict, Set

# We need access to the sessions in mafia_cog
# We can import them because they are module-level
from cogs.mafia_cog import _sessions, _web_sessions, _rejoin_tokens, _ws_clients, Player

log = logging.getLogger("mafia_routes")

mafia_routes = web.RouteTableDef()

def get_session_and_id(request):
    session_id = request.match_info['session_id']
    guild_id = _web_sessions.get(session_id)
    if not guild_id:
        return None, session_id
    session = _sessions.get(guild_id)
    return session, session_id

@mafia_routes.get('/mafia/{session_id}')
async def mafia_shell(request):
    """Serve the game HTML shell."""
    return web.FileResponse('templates/mafia/index.html')

@mafia_routes.get('/mafia/{session_id}/state')
async def mafia_state(request):
    """Return full game state as JSON."""
    session, session_id = get_session_and_id(request)
    if not session:
        return web.json_response({"error": "Session not found"}, status=404)
    
    # Check for player_id in query (for omniscience check)
    player_id = request.query.get('player_id')
    is_spectator = True
    if player_id:
        p_id = int(player_id)
        if p_id in session.players:
            is_spectator = False

    # Serialize session
    # We'll use the cog's conversion logic
    cog = request.app['bot'].get_cog("MafiaCog")
    if not cog:
        return web.json_response({"error": "Mafia cog not loaded"}, status=500)
    
    state = cog._session_to_dict(session)
    
    # Hide roles if not spectator or dead
    if not is_spectator:
        p_id = int(player_id)
        player = session.players.get(p_id)
        if player and player.alive:
            # Hide other roles
            for pid, pdata in state['players'].items():
                if int(pid) != p_id:
                    # Mafia see other mafia
                    if player.role == "Mafia" and pdata['role'] == "Mafia":
                        continue
                    pdata['role'] = "???"
                    pdata['team'] = "???"
    
    return web.json_response(state)

@mafia_routes.post('/mafia/{session_id}/join')
async def mafia_join(request):
    """Player joins via web."""
    session, session_id = get_session_and_id(request)
    if not session:
        return web.json_response({"error": "Session not found"}, status=404)
    
    data = await request.json()
    user_id = data.get('user_id')
    nickname = data.get('nickname')
    
    if not user_id or not nickname:
        return web.json_response({"error": "Missing user_id or nickname"}, status=400)
    
    # Generate rejoin token
    token = str(uuid.uuid4()).replace('-', '')[:32]
    
    # Store in rejoin tokens (Inverted as per plan: {token: user_id})
    if session_id not in _rejoin_tokens:
        _rejoin_tokens[session_id] = {}
    _rejoin_tokens[session_id][token] = int(user_id)
    
    # Add to session if lobby
    if session.phase == "lobby":
        if int(user_id) not in session.players:
            session.players[int(user_id)] = Player(int(user_id), nickname)
            # Update Discord embed
            cog = request.app['bot'].get_cog("MafiaCog")
            if cog:
                asyncio.create_task(cog._update_lobby_embed(session))
                asyncio.create_task(cog._broadcast_event(session_id, "player_joined", {
                    "player_id": int(user_id),
                    "nickname": nickname
                }))
    
    return web.json_response({"token": token, "player_id": int(user_id)})

@mafia_routes.post('/mafia/{session_id}/action')
async def mafia_action(request):
    """Submit a night action or day vote."""
    session, session_id = get_session_and_id(request)
    if not session:
        return web.json_response({"error": "Session not found"}, status=404)
    
    data = await request.json()
    player_id = data.get('player_id')
    token = data.get('rejoin_token')
    action_type = data.get('action_type') # 'vote' or 'night_action'
    target_id = data.get('target_id')
    
    # Validate token
    stored_tokens = _rejoin_tokens.get(session_id, {})
    if stored_tokens.get(token) != int(player_id):
        return web.json_response({"error": "Invalid rejoin token"}, status=403)
    
    cog = request.app['bot'].get_cog("MafiaCog")
    if not cog: return web.json_response({"error": "Cog error"}, status=500)

    player = session.players.get(int(player_id))
    if not player or not player.alive:
        return web.json_response({"error": "Player dead or not found"}, status=400)

    if action_type == 'vote' and session.phase == 'day':
        session.day_votes[int(player_id)] = int(target_id)
        # Broadcast update
        counts = {}
        for tid in session.day_votes.values():
            counts[tid] = counts.get(tid, 0) + 1
        asyncio.create_task(cog._broadcast_event(session_id, "vote_update", {"votes": counts, "type": "day"}))
        return web.json_response({"status": "ok"})
    
    elif action_type == 'night_action' and session.phase == 'night':
        role = player.role
        if role == "Mafia":
            session.mafia_votes[int(player_id)] = int(target_id)
            # Broadcast mafia votes to other mafia
            counts = {}
            for tid in session.mafia_votes.values():
                counts[tid] = counts.get(tid, 0) + 1
            # Filter broadcast only to mafia? Harder via generic broadcast. 
            # We'll just update everyone; front-end handles visibility.
            asyncio.create_task(cog._broadcast_event(session_id, "vote_update", {"votes": counts, "type": "mafia"}))
        else:
            session.night_actions[role] = int(target_id)
        
        return web.json_response({"status": "ok"})

    return web.json_response({"error": "Invalid action or phase"}, status=400)

@mafia_routes.get('/mafia/ws/{session_id}')
async def mafia_ws(request):
    """WebSocket for real-time push events."""
    session_id = request.match_info['session_id']
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    if session_id not in _ws_clients:
        _ws_clients[session_id] = set()
    _ws_clients[session_id].add(ws)
    
    log.info(f"WebSocket connected for session {session_id}")
    
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                mtype = data.get('type')
                
                if mtype == 'ping':
                    await ws.send_str(json.dumps({"type": "pong"}))
                
                elif mtype == 'chat':
                    # Handle chat broadcast
                    cog = request.app['bot'].get_cog("MafiaCog")
                    if cog:
                        player_id = data.get('player_id')
                        token = data.get('rejoin_token')
                        text = data.get('text')
                        
                        # Validate
                        stored_tokens = _rejoin_tokens.get(session_id, {})
                        if stored_tokens.get(token) == int(player_id):
                            guild_id = _web_sessions.get(session_id)
                            session = _sessions.get(guild_id)
                            player = session.players.get(int(player_id))
                            nickname = player.display_name if player else "Spectator"
                            
                            await cog._broadcast_event(session_id, "chat_message", {
                                "sender_id": int(player_id),
                                "nickname": nickname,
                                "text": text,
                                "timestamp": int(asyncio.get_running_loop().time()),
                                "is_ghost": not player.alive if player else True
                            })
            
            elif msg.type == WSMsgType.ERROR:
                log.error(f"WS connection closed with exception {ws.exception()}")
    finally:
        _ws_clients[session_id].remove(ws)
        if not _ws_clients[session_id]:
            del _ws_clients[session_id]
            
    return ws
