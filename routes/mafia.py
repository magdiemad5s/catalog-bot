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
    
    # Bug 4: player_id cast without validation
    player_id = request.query.get('player_id')
    is_spectator = True
    p_id = None
    if player_id:
        try:
            p_id = int(player_id)
            if p_id in session.players:
                is_spectator = False
        except (ValueError, TypeError):
            pass # Treat as spectator on bad input

    # Serialize session
    # We'll use the cog's conversion logic
    cog = request.app['bot'].get_cog("MafiaCog")
    if not cog:
        return web.json_response({"error": "Mafia cog not loaded"}, status=500)
    
    state = cog._session_to_dict(session)
    
    me = session.players.get(p_id) if not is_spectator else None

    # Hide roles if not spectator or dead, and ONLY if the game has started
    if session.phase != "lobby":
        for pid, pdata in state['players'].items():
            pid_int = int(pid)
            other_player = session.players.get(pid_int)
            
            # Visibility rules:
            # 1. You always see your own role
            if pid_int == p_id:
                continue
            
            # 2. Mafia see other alive Mafia
            if not is_spectator and me:
                if me.alive and me.role == "Mafia" and other_player and other_player.role == "Mafia" and other_player.alive:
                    continue
                
                # 3. Dead players see ALL other DEAD players' roles
                if not me.alive and other_player and not other_player.alive:
                    continue

            # Default: Mask role
            pdata['role'] = "???"
            pdata['team'] = "???"
    elif session.phase == "lobby":
        # In lobby, hide actual roles (since they default to Villager but aren't assigned)
        for pdata in state['players'].values():
            pdata['role'] = "Lobby"
            pdata['team'] = "Lobby"

    # Security: Mask night actions and votes
    if not is_spectator:
        me = session.players.get(int(player_id))
        
        # Mask night_actions (Everyone only sees their own action)
        new_night_actions = {}
        str_player_id = str(player_id)
        if str_player_id in state.get('night_actions', {}):
             new_night_actions[str_player_id] = state['night_actions'][str_player_id]
        state['night_actions'] = new_night_actions
        
        # Mask mafia_votes (Only Mafia see mafia_votes)
        if me and me.role != "Mafia":
            state['mafia_votes'] = {}
            
    else:
        # Spectators see nothing secret
        state['night_actions'] = {}
        state['mafia_votes'] = {}
        # They can see day_votes (public)
    
    return web.json_response(state)

@mafia_routes.post('/mafia/{session_id}/join')
async def mafia_join(request):
    """Player joins via web."""
    session, session_id = get_session_and_id(request)
    if not session:
        return web.json_response({"error": "Session not found"}, status=404)
    
    data = await request.json()
    
    # Bug 6: user_id validation
    try:
        user_id = int(data.get('user_id', 0))
        if user_id <= 0 or user_id > 2**53:
            return web.json_response({"error": "Invalid user_id"}, status=400)
    except (TypeError, ValueError):
        return web.json_response({"error": "user_id must be an integer"}, status=400)

    # Bug 9: Nickname length validation
    nickname = str(data.get('nickname', '')).strip()[:32]
    if len(nickname) < 1:
        return web.json_response({"error": "Nickname too short"}, status=400)
    
    user_id_int = int(user_id)
    # Add to session if lobby
    if session.phase == "lobby":
        async with session.join_lock:
            if user_id_int not in session.players:
                # Check for duplicate nickname
                if any(p.display_name.lower() == nickname.lower() for p in session.players.values()):
                    return web.json_response({"error": "This nickname is already taken!"}, status=400)
                
                session.players[user_id_int] = Player(user_id_int, nickname)
                # Update Discord embed
                cog = request.app['bot'].get_cog("MafiaCog")
                if cog:
                    asyncio.create_task(cog._update_lobby_embed(session))
                    asyncio.create_task(cog._broadcast_event(session_id, "player_joined", {
                        "player_id": user_id_int,
                        "nickname": nickname
                    }))

    # Generate rejoin token
    token = str(uuid.uuid4()).replace('-', '')[:32]
    
    # Store in rejoin tokens (Inverted as per plan: {token: user_id})
    if session_id not in _rejoin_tokens:
        _rejoin_tokens[session_id] = {}
    _rejoin_tokens[session_id][token] = user_id_int
    
    return web.json_response({"token": token, "player_id": user_id_int})

@mafia_routes.post('/mafia/{session_id}/vote_start')
async def mafia_vote_start(request):
    """Player votes to start the game."""
    session, session_id = get_session_and_id(request)
    if not session or session.phase != "lobby":
        return web.json_response({"error": "Invalid session state"}, status=400)
    
    data = await request.json()
    
    # Bug 4: player_id validation
    try:
        player_id = int(data.get('player_id', 0))
    except (TypeError, ValueError):
        return web.json_response({"error": "Invalid player_id"}, status=400)

    if player_id not in session.players:
        return web.json_response({"error": "Not a participant"}, status=403)

    cog = request.app['bot'].get_cog("MafiaCog")
    if cog:
        await cog._handle_start_vote(session, player_id)
    
    return web.json_response({"status": "voted"})

@mafia_routes.post('/mafia/{session_id}/leave')
async def mafia_leave(request):
    """Player leaves the lobby."""
    session, session_id = get_session_and_id(request)
    if not session or session.phase != "lobby":
        return web.json_response({"error": "Cannot leave now"}, status=400)
    
    data = await request.json()
    player_id = data.get('player_id')
    token = data.get('rejoin_token')
    
    # Bug 5: player_id bounds check and validation
    try:
        player_id_int = int(player_id)
        if player_id_int <= 0 or player_id_int > 2**53:
            return web.json_response({"error": "Invalid player_id"}, status=400)
    except (TypeError, ValueError):
        return web.json_response({"error": "Invalid player_id"}, status=400)
    
    # Validate token
    stored_tokens = _rejoin_tokens.get(session_id, {})
    if stored_tokens.get(token) != player_id_int:
        return web.json_response({"error": "Invalid token"}, status=403)
    
    cog = request.app['bot'].get_cog("MafiaCog")
    if cog:
        await cog._handle_leave(session, player_id_int)
    
    return web.json_response({"status": "left"})

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
    
    # Bug 3: Validate player_id before cast
    try:
        player_id_int = int(player_id)
        if player_id_int <= 0 or player_id_int > 2**53:
            return web.json_response({"error": "Invalid player_id"}, status=400)
    except (TypeError, ValueError):
        return web.json_response({"error": "player_id required"}, status=400)

    # Validate token
    stored_tokens = _rejoin_tokens.get(session_id, {})
    if stored_tokens.get(token) != player_id_int:
        return web.json_response({"error": "Invalid rejoin token"}, status=403)
    
    cog = request.app['bot'].get_cog("MafiaCog")
    if not cog: return web.json_response({"error": "Cog error"}, status=500)

    player = session.players.get(player_id_int)
    if not player or not player.alive:
        return web.json_response({"error": "Player dead or not found"}, status=400)

    # Bug 6: target_id validation
    target_id = data.get('target_id')
    if target_id is None:
        return web.json_response({"error": "target_id required"}, status=400)
    try:
        target_id_int = int(target_id)
    except (TypeError, ValueError):
        return web.json_response({"error": "Invalid target_id"}, status=400)

    if action_type == 'vote' and session.phase == 'day':
        # Bug 7: Validate target existence
        target = session.players.get(target_id_int)
        if not target or not target.alive:
            return web.json_response({"error": "Invalid target"}, status=400)

        session.day_votes[player_id_int] = target_id_int
        # Broadcast update
        counts = {}
        for tid in session.day_votes.values():
            counts[tid] = counts.get(tid, 0) + 1
        asyncio.create_task(cog._broadcast_event(session_id, "vote_update", {"votes": counts, "type": "day"}))
        return web.json_response({"status": "ok"})
    
    elif action_type == 'night_action' and session.phase == 'night':
        # Bug 7: Validate target existence
        target = session.players.get(target_id_int)
        if not target or not target.alive:
            return web.json_response({"error": "Invalid target"}, status=400)

        role = player.role
        if role == "Vigilante" and player.used_vigilante_shot:
            return web.json_response({"error": "You already used your shot!"}, status=400)
            
        if role == "Doctor" and target_id_int == player_id_int and player.last_protected == player_id_int:
            return web.json_response({"error": "Cannot self-protect twice in a row"}, status=400)
            
        if role == "Detective" and target_id_int == player_id_int:
            return web.json_response({"error": "Cannot investigate yourself"}, status=400)

        if role == "Mafia":
            # Bug 7: Mafia cannot target self
            if target_id_int == player_id_int:
                return web.json_response({"error": "Cannot target yourself"}, status=400)
            
            session.mafia_votes[player_id_int] = target_id_int
            # Broadcast mafia votes to other mafia
            counts = {}
            for tid in session.mafia_votes.values():
                counts[tid] = counts.get(tid, 0) + 1
            asyncio.create_task(cog._broadcast_event(session_id, "vote_update", {"votes": counts, "type": "mafia"}))
        else:
            session.night_actions[player_id_int] = target_id_int
        
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
                
                elif mtype == 'identify':
                    # Bug 17: Handle identify message for reconnects
                    player_id = data.get('player_id')
                    token = data.get('rejoin_token')
                    try:
                        pid_int = int(player_id) if player_id else None
                    except (ValueError, TypeError):
                        pid_int = None
                    
                    stored = _rejoin_tokens.get(session_id, {})
                    if pid_int and token and stored.get(token) == pid_int:
                        ws['player_id'] = pid_int
                        await ws.send_str(json.dumps({"type": "identified", "player_id": pid_int}))
                
                elif mtype == 'chat':
                    # Handle chat broadcast
                    cog = request.app['bot'].get_cog("MafiaCog")
                    if cog:
                        # Bug 3: None checks
                        player_id = data.get('player_id')
                        try:
                            player_id_int = int(player_id) if player_id else None
                        except (ValueError, TypeError):
                            await ws.send_str(json.dumps({"type": "error", "message": "Invalid player_id"}))
                            continue
                            
                        token = data.get('rejoin_token')
                        text = data.get('text', '').strip()
                        
                        if not player_id_int or not text:
                            continue
                        
                        # Validate text length
                        text = text[:500]
                        
                        # Bug 1: Token validation
                        stored_tokens = _rejoin_tokens.get(session_id, {})
                        if stored_tokens.get(token) != player_id_int:
                            await ws.send_str(json.dumps({"type": "error", "message": "Unauthorized"}))
                            continue

                        # Bug 5: Session lookup guards
                        guild_id = _web_sessions.get(session_id)
                        session = _sessions.get(guild_id) if guild_id else None
                        if not session:
                            continue
                        
                        channel = data.get('channel', 'global')
                        player = session.players.get(player_id_int)
                        nickname = player.display_name if player else "Spectator"
                        
                        if channel == "mafia" and (not player or player.role != "Mafia"):
                            continue
                        if channel == "ghost" and player and player.alive:
                            continue
                        
                        payload_data = {
                            "sender_id": player_id_int,
                            "nickname": nickname,
                            "text": text,
                            "timestamp": int(asyncio.get_running_loop().time()),
                            "is_ghost": not player.alive if player else True,
                            "channel": channel
                        }
                        payload = json.dumps({"type": "chat_message", "data": payload_data})
                        
                        clients = _ws_clients.get(session_id, set())
                        to_remove = set()
                        for _ws in clients:
                            target_pid = _ws.get('player_id')
                            target_p = session.players.get(target_pid) if target_pid else None
                            
                            if channel == "mafia":
                                if not target_p or target_p.role != "Mafia":
                                    continue
                            elif channel == "ghost":
                                if target_p and target_p.alive:
                                    continue
                                    
                            try:
                                await _ws.send_str(payload)
                            except:
                                to_remove.add(_ws)
                                
                        for _ws in to_remove: 
                            clients.remove(_ws)
            
            elif msg.type == WSMsgType.ERROR:
                log.error(f"WS connection closed with exception {ws.exception()}")
    finally:
        _ws_clients[session_id].remove(ws)
        if not _ws_clients[session_id]:
            del _ws_clients[session_id]
            
    return ws
