"""Self-play agentic reasoning: LLM strategists write sword bots, fight each other, and reason about why.

  python -m vertex_training agentic run [--rounds 10] [--port 25600] [--backend claude|noop] [--resume RUN]
  python -m vertex_training agentic status [--resume RUN]

The RL policy learns reflexes; it can't say why it does anything. Here each strategist (an LLM with
its own persona) keeps one bot as a readable Python program. Every round:

  1. tournament   every strategist's current bot fights every other one, its own previous version, and
                  fixed anchors (the scripted chase / crit / punish / eager bots and the RL champion), on
                  an arena server of its own (training/agentic-arena.ps1; it never touches the trainer's)
  2. analysis     for each bot, per opponent: score, and the same breakdown `arena analyse` gives for
                  duels (whiffs, exchanges opened and won, combos, range, aim)
  3. reasoning    each strategist reads its bot's results and writes a diagnosis, a falsifiable
                  hypothesis with the effect it expects, and the next version of its code
  4. verdict      next round measures whether that effect happened; the hypothesis and its measured
                  verdict go into a shared notebook every strategist reads

Code stays private to its strategist (shared code would make every bot converge); only claims and the
evidence for or against them are shared. Anchors score every bot on the same fixed scale, so getting
better against each other can be told apart from getting better.

Layout: agentic/ sandbox.py (checks and loads bot code), view.py (what a bot sees), match.py (the
tournament, run in a child process with a time limit), strategist.py (LLM backends), loop.py (rounds,
notebook, storage). Runs live in selfplay/agentic/<run>/.
"""
