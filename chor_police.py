"""
🕵️ Police • Thief • Soldier — a multiplayer party game built with Streamlit.

Everyone opens the same app link on their OWN phone:
  • One person creates a room and shares the 4-letter room code.
  • Everyone else joins with their name + the code.
  • Roles are dealt randomly each round and shown privately on each phone.

Run:   pip install streamlit
       streamlit run app.py
(Players on the same Wi-Fi can open the "Network URL" Streamlit prints.)
"""

import html
import random
import re
import threading
import time
import streamlit as st

import streamlit as st

# Configure the page title and favicon
st.set_page_config(
    page_title="Harshad's app",
    page_icon="🕵️",
    layout="wide"
)


with open("police_theme.css") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# Optional tape banner, e.g. on the start screen
st.markdown('<div class="police-tape">POLICE LINE DO NOT CROSS</div>', unsafe_allow_html=True)
# ─────────────────────────── Game settings ───────────────────────────
MIN_PLAYERS = 3            # Police + Thief + at least 1 Soldier
MIN_ROUNDS = 5             # change these two to allow other round counts
MAX_ROUNDS = 10
POLICE_WIN_POINTS = 1000   # police guessed the thief
THIEF_WIN_POINTS = 1000    # police guessed wrong
SOLDIER_POINTS = 500       # soldiers always get this
ROOM_TTL_SECONDS = 6 * 60 * 60   # old rooms are cleaned up after 6 hours
REFRESH_EVERY = 2          # seconds between automatic screen refreshes

ROLES = {
    "Police": {"icon": "👮", "color": "#2563eb", "tip": "Find the thief and win 1000 points!"},
    "Thief": {"icon": "🦹", "color": "#dc2626", "tip": "Stay hidden — if the police guess wrong, you win 1000!"},
    "Soldier": {"icon": "💂", "color": "#16a34a", "tip": "You earn 500 points this round, no matter what."},
}


# ─────────────────────── Shared server-side state ───────────────────────
# Every phone is a separate Streamlit session, so the game lives in a
# server-wide dictionary that all sessions can read and update.
@st.cache_resource
def _store():
    return {"rooms": {}, "lock": threading.RLock()}


STORE = _store()
ROOMS = STORE["rooms"]
LOCK = STORE["lock"]


# ───────────────────────────── Game logic ─────────────────────────────
def _new_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no confusing 0/O/1/I
    while True:
        code = "".join(random.choices(alphabet, k=4))
        if code not in ROOMS:
            return code


def create_room(host, rounds):
    with LOCK:
        now = time.time()
        for c in [c for c, r in ROOMS.items() if now - r["created"] > ROOM_TTL_SECONDS]:
            del ROOMS[c]
        code = _new_code()
        ROOMS[code] = {
            "code": code,
            "host": host,
            "players": [host],
            "total_rounds": rounds,
            "round": 0,
            "phase": "lobby",       # lobby → reveal → guess → result → (next round) … → final
            "roles": {},
            "ready": set(),
            "scores": {host: 0},
            "round_points": {},
            "guess": None,
            "created": now,
        }
        return code


def join_room(code, name):
    """Returns (error_message, canonical_name)."""
    with LOCK:
        room = ROOMS.get(code)
        if room is None:
            return "Room not found. Check the code.", None
        existing = next((p for p in room["players"] if p.lower() == name.lower()), None)
        if room["phase"] == "lobby":
            if existing:
                return "That name is already taken in this room.", None
            room["players"].append(name)
            room["scores"][name] = 0
            return None, name
        if existing:                       # game running → treat as reconnect
            return None, existing
        return "This game has already started. To rejoin, use your original name.", None


def leave_room(code, me):
    with LOCK:
        room = ROOMS.get(code)
        if room and room["phase"] == "lobby" and me in room["players"]:
            room["players"].remove(me)
            room["scores"].pop(me, None)
            if not room["players"]:
                del ROOMS[code]
            elif room["host"] == me:
                room["host"] = room["players"][0]
    st.session_state.pop("code", None)
    st.session_state.pop("me", None)


def _who(room, role):
    return next(p for p, r in room["roles"].items() if r == role)


def _start_round(room):
    """Deal roles. Caller must hold LOCK."""
    room["round"] += 1
    order = room["players"][:]
    random.shuffle(order)
    roles = {p: "Soldier" for p in order}   # everyone beyond police & thief is a soldier
    roles[order[0]] = "Police"
    roles[order[1]] = "Thief"
    room.update(roles=roles, ready=set(), guess=None, round_points={}, phase="reveal")


def start_game(code):
    with LOCK:
        room = ROOMS.get(code)
        if not room or room["phase"] != "lobby" or len(room["players"]) < MIN_PLAYERS:
            return
        room["scores"] = {p: 0 for p in room["players"]}
        room["round"] = 0
        _start_round(room)


def mark_ready(code, me, peek_key):
    st.session_state[peek_key] = False       # hide the role again
    with LOCK:
        room = ROOMS.get(code)
        if room and room["phase"] == "reveal":
            room["ready"].add(me)
            if len(room["ready"]) >= len(room["players"]):
                room["phase"] = "guess"


def submit_guess(code, guess_key):
    guess = st.session_state.get(guess_key)
    with LOCK:
        room = ROOMS.get(code)
        if not room or room["phase"] != "guess" or not guess:
            return
        correct = guess == _who(room, "Thief")
        points = {}
        for player, role in room["roles"].items():
            if role == "Police":
                points[player] = POLICE_WIN_POINTS if correct else 0
            elif role == "Thief":
                points[player] = 0 if correct else THIEF_WIN_POINTS
            else:
                points[player] = SOLDIER_POINTS
            room["scores"][player] += points[player]
        room.update(guess=guess, round_points=points, phase="result")


def next_round(code):
    with LOCK:
        room = ROOMS.get(code)
        if not room or room["phase"] != "result":
            return
        if room["round"] >= room["total_rounds"]:
            room["phase"] = "final"
        else:
            _start_round(room)


def play_again(code):
    with LOCK:
        room = ROOMS.get(code)
        if not room or room["phase"] != "final":
            return
        room.update(phase="lobby", round=0, roles={}, ready=set(), guess=None, round_points={})
        room["scores"] = {p: 0 for p in room["players"]}


# ───────────────────────────── UI helpers ─────────────────────────────
def md(text):
    """Escape markdown characters in player-supplied names."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>~$])", r"\\\1", text)


def _set(key, value):
    st.session_state[key] = value


def role_card(role):
    r = ROLES[role]
    st.markdown(
        f'<div style="background:{r["color"]};color:white;padding:28px 16px;'
        f'border-radius:18px;text-align:center;">'
        f'<div style="font-size:64px;line-height:1.1">{r["icon"]}</div>'
        f'<div style="font-size:32px;font-weight:700">{html.escape(role)}</div>'
        f'<div style="opacity:.92;margin-top:6px">{html.escape(r["tip"])}</div></div>',
        unsafe_allow_html=True,
    )


def role_panel(room, me):
    """Private role viewer with show/hide. Returns (is_shown, state_key)."""
    key = f"peek_{room['code']}_{room['round']}"
    shown = st.session_state.get(key, False)
    if shown:
        role_card(room["roles"][me])
        st.button("🙈 Hide my role", key=f"hide_{key}", on_click=_set, args=(key, False))
    else:
        st.info("🔒 Your role is hidden. Make sure nobody can see your screen, then tap below.")
        st.button("👀 Show my role", key=f"show_{key}", on_click=_set, args=(key, True))
    return shown, key


def scoreboard(room, me):
    ranked = sorted(room["scores"].items(), key=lambda kv: -kv[1])
    medals = ["🥇", "🥈", "🥉"]
    for i, (player, score) in enumerate(ranked):
        medal = medals[i] if i < 3 else "▫️"
        you = " *(you)*" if player == me else ""
        st.markdown(f"{medal} **{md(player)}**{you} — {score} pts")


# ───────────────────────────── Screens ─────────────────────────────
def home_screen():
    st.title("🕵️ Police • Thief • Soldier")
    st.caption("Open this page on your own phone. Nobody else should see your screen!")

    name = st.text_input("Your name", max_chars=20).strip()
    tab_create, tab_join = st.tabs(["➕ Create room", "🔑 Join room"])

    with tab_create:
        rounds = st.slider("Number of rounds", MIN_ROUNDS, MAX_ROUNDS, MIN_ROUNDS)
        if st.button("Create room", type="primary"):
            if not name:
                st.error("Please enter your name first.")
            else:
                code = create_room(name, rounds)
                st.session_state.code, st.session_state.me = code, name
                st.rerun()

    with tab_join:
        code_in = st.text_input("Room code", max_chars=4).strip().upper()
        if st.button("Join room", type="primary"):
            if not name:
                st.error("Please enter your name first.")
            elif len(code_in) != 4:
                st.error("Enter the 4-character room code.")
            else:
                error, canonical = join_room(code_in, name)
                if error:
                    st.error(error)
                else:
                    st.session_state.code, st.session_state.me = code_in, canonical
                    st.rerun()

    with st.expander("📖 How to play"):
        st.markdown(
            f"""
- **{MIN_PLAYERS}+ players.** Each round: 1 👮 Police, 1 🦹 Thief, everyone else 💂 Soldier.
- Roles are secret, except the **Police**, who is announced to everyone.
- The Police must guess who the Thief is.
- ✅ Correct guess → Police **{POLICE_WIN_POINTS}**, Thief 0.
- ❌ Wrong guess → Police 0, Thief **{THIEF_WIN_POINTS}**.
- 💂 Soldiers always get **{SOLDIER_POINTS}**.
- Most points after the last round wins!
"""
        )


@st.fragment(run_every=REFRESH_EVERY)
def game_screen():
    code = st.session_state.get("code")
    me = st.session_state.get("me")
    room = ROOMS.get(code) if code else None

    if room is None or me not in room["players"]:
        st.session_state.pop("code", None)
        st.session_state.pop("me", None)
        st.rerun()          # back to the home screen (full-app rerun)
        return

    phase = room["phase"]
    st.markdown(f"## 🕵️ Room `{code}`")
    st.caption(f"Playing as **{md(me)}**")
    if phase != "lobby":
        shown_round = min(room["round"], room["total_rounds"])
        st.progress(shown_round / room["total_rounds"], text=f"Round {shown_round} of {room['total_rounds']}")

    # ── Lobby ──
    if phase == "lobby":
        st.subheader("Waiting for players")
        st.write(f"Share the code **{code}** with your friends. They open this same page and join.")
        for p in room["players"]:
            st.markdown(f"- {md(p)}" + (" 👑 *(host)*" if p == room["host"] else ""))
        n = len(room["players"])
        st.caption(f"{n} player(s) • {room['total_rounds']} rounds • each round: 1 Police, 1 Thief, {max(n - 2, 0)} Soldier(s)")
        if me == room["host"]:
            if n < MIN_PLAYERS:
                st.warning(f"Need at least {MIN_PLAYERS} players to start.")
            st.button("🚀 Start game", type="primary", disabled=n < MIN_PLAYERS,
                      on_click=start_game, args=(code,))
        else:
            st.info("Waiting for the host to start the game…")
        st.button("Leave room", on_click=leave_room, args=(code, me))
        return

    # ── Reveal / Guess / Result ──
    if phase in ("reveal", "guess"):
        police = _who(room, "Police")
        st.success(f"👮 **{md(police)}** is the Police this round!")

    if phase == "reveal":
        st.subheader("Check your secret role")
        if me in room["ready"]:
            st.success("You're ready. Your role is hidden. ✅")
            waiting = [p for p in room["players"] if p not in room["ready"]]
            st.write(f"Waiting for: {', '.join(md(p) for p in waiting)}")
        else:
            shown, key = role_panel(room, me)
            if shown:
                st.button("✅ I've seen it — hide it & I'm ready", type="primary",
                          on_click=mark_ready, args=(code, me, key))
        st.caption(f"{len(room['ready'])}/{len(room['players'])} players ready")

    elif phase == "guess":
        police = _who(room, "Police")
        if me == police:
            st.subheader("🔍 Who is the thief?")
            gkey = f"guess_{code}_{room['round']}"
            suspects = [p for p in room["players"] if p != me]
            st.radio("Pick your suspect", suspects, index=None, key=gkey)
            st.button("🚨 Arrest!", type="primary", disabled=st.session_state.get(gkey) is None,
                      on_click=submit_guess, args=(code, gkey))
            st.caption("Careful — a wrong guess gives you 0 points and the thief 1000!")
        else:
            st.subheader(f"Waiting for {md(police)} to guess…")
            role_panel(room, me)

    elif phase == "result":
        police, thief = _who(room, "Police"), _who(room, "Thief")
        if room["guess"] == thief:
            st.success(f"✅ {md(police)} guessed **{md(room['guess'])}** — correct! The thief is caught.")
        else:
            st.error(f"❌ {md(police)} guessed **{md(room['guess'])}** — wrong! The real thief was **{md(thief)}**.")
        st.subheader("This round")
        for p, role in room["roles"].items():
            st.markdown(f"{ROLES[role]['icon']} **{md(p)}** ({role}) — **+{room['round_points'][p]}**")
        st.subheader("Scoreboard")
        scoreboard(room, me)
        last = room["round"] >= room["total_rounds"]
        if me == room["host"]:
            st.button("🏁 See final results" if last else "▶️ Next round", type="primary",
                      on_click=next_round, args=(code,))
        else:
            st.info("Waiting for the host to continue…")

    elif phase == "final":
        best = max(room["scores"].values())
        winners = [p for p, s in room["scores"].items() if s == best]
        st.header("🏆 Game over!")
        st.success("Winner: " + ", ".join(f"**{md(w)}**" for w in winners) + f" with {best} pts")
        flag = f"balloons_{code}"
        if not st.session_state.get(flag):
            st.session_state[flag] = True
            st.balloons()
        scoreboard(room, me)
        if me == room["host"]:
            st.button("🔄 Play again (same players)", type="primary", on_click=play_again, args=(code,))
        else:
            st.info("Waiting for the host to start a new game…")
        st.button("Leave room", on_click=leave_room, args=(code, me))

    if phase in ("reveal", "guess"):
        with st.expander("📊 Scoreboard"):
            scoreboard(room, me)


# ───────────────────────────── Entry point ─────────────────────────────
if st.session_state.get("code") and st.session_state.get("me"):
    game_screen()
else:
    home_screen()


