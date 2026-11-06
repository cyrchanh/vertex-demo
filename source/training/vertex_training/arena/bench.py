"""Milestone 3: bots fight from Python; outcomes logged; ticks per second measured.

  python -m vertex_training arena bench [--episodes 6] [--matchup chase-vs-random]
"""

import time

from . import scripted
from .env import SwordArenaEnv

MATCHUPS = {
    "chase-vs-random": (lambda: scripted.ChaseFighter(seed=1), lambda: scripted.RandomFighter(seed=2)),
    "chase-vs-chase": (lambda: scripted.ChaseFighter(seed=1), lambda: scripted.ChaseFighter(seed=2)),
    "chase-vs-idle": (lambda: scripted.ChaseFighter(seed=1), lambda: scripted.idle),
    "crit-vs-idle": (lambda: scripted.ChaseFighter(seed=1, crits=True, strafe=False), lambda: scripted.idle),
    "crit-vs-chase":(lambda: scripted.ChaseFighter(seed=1, crits=True), lambda: scripted.ChaseFighter(seed=2)),
    "random-vs-random": (lambda: scripted.RandomFighter(seed=1), lambda: scripted.RandomFighter(seed=2)),
}


def run(episodes=6, matchup="chase-vs-random", port=25570, max_ticks=1200, log=print, watch=False, pause=3.0):
    """watch: 20 ticks per second and a pause between fights, for someone spectating on localhost:25566."""
    env = SwordArenaEnv(port=port, max_ticks=max_ticks, repeat=1, reaction_steps=0, seed=0)
    make_a, make_b = MATCHUPS[matchup]
    totals = {"ticks": 0, "seconds": 0.0, "wins": [0, 0], "draws": 0}
    try:
        for ep in range(episodes):
            if watch and ep > 0:
                time.sleep(pause)
            fighters = [make_a(), make_b()]
            env.reset()
            if watch and ep == 0:
                env.client.realtime(True)   # the connection opens on the first reset
            state = env.state
            start = time.perf_counter()
            hits = [0, 0]
            crits = [0, 0]
            damage = [0.0, 0.0]
            while env.agents:
                actions = {agent: fighters[i](state, i) for i, agent in enumerate(env.possible_agents)}
                env.step(actions)
                state = env.state
                for e in state["events"]:
                    if e["type"] == "hit":
                        hits[e["attacker"]] += 1
                        crits[e["attacker"]] += int(e["crit"])
                        damage[e["attacker"]] += e["damage"]
            seconds = time.perf_counter() - start
            winner = state["winner"]
            if winner >= 0:
                totals["wins"][winner] += 1
            else:
                totals["draws"] += 1
            totals["ticks"] += state["tick"]
            totals["seconds"] += seconds
            log(f"  episode {ep + 1}: {state['tick']:4d} ticks in {seconds:5.2f} s "
                f"({state['tick'] / seconds:6.0f} ticks/s)  winner {'bot ' + str(winner) if winner >= 0 else 'none'}  "
                f"hits {hits[0]}/{hits[1]}  crits {crits[0]}/{crits[1]}  damage dealt {damage[0]:.1f}/{damage[1]:.1f} HP")
    finally:
        env.close()
    rate = totals["ticks"] / max(totals["seconds"], 1e-9)
    log(f"  {matchup}: wins {totals['wins'][0]}-{totals['wins'][1]}, draws {totals['draws']}; "
        f"{rate:.0f} ticks/s = {rate / 20:.0f}x real time on one server")
    return totals
