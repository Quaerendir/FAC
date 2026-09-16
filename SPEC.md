# FAC — Game Engine Specification (derived from the BASIC listing)

Target: pure-logic engine module in Python 3.11+ (stage 3, `fac/engine.py`), no
rendering, no I/O. Rendering, sound and input (pygame) are stage 4 and consume
the engine through the API in §8.

Original: J.B. Wiśniewski, 1992, Atari 8-bit, Atari BASIC, published as a
type-in listing in Tajemnice ATARI 2/92 (`2_92_fac.html`). The reference text
is `FAC.LST`, the program as LISTed by Atari BASIC, from the archive's disk
image (`2_92_listingi.zip` → `2_92.atr`), verified against the magazine's
two-letter line codes (`tools/line_codes.py`, algorithm from TA 2/91). Every rule below is
read from that listing; `L205` means BASIC line 205. Nothing is guessed except
the *durations* of things (§7), which depend on the speed of the BASIC
interpreter and are marked **[EST]**.

Unlike Heartlight there is no machine code: the whole game is 140 lines of
BASIC, the "engine" is the main loop L200..L355 and the subroutines L360..L725.

---

## 1. Data model

### 1.1 Screen = grid
GRAPHICS 18 (ANTIC mode 7): 20 columns × 12 rows of 16×16-pixel characters,
`EK` = screen memory (L10). **The screen memory is the game state**: the
terrain test `K=PEEK(EK+20*Y+X)` (L540) reads it back, the hero is a character
printed into it (L205), the counters are digits printed into the top wall row
(L195, L225, L290), an opened trapdoor is a POKE 0 into it (L480). The engine
therefore keeps a 240-byte `screen` of GRAPHICS 18 screen codes, index
`y*20 + x`, y=0 at the top.

A screen code is `colour<<6 | glyph` (glyph 0..63). `? #6;` converts ATASCII
the usual way: $20..$5F → glyph 0..63 colour 0; $00..$1F (control chars) →
colour 1; $60..$7F → colour 1; inverse (bit 7) → colour 2 or 3. The colour
registers 708..712 are POKEd per level (L150..L155): 708 = walls, 709 = hero
("FAC"), 710 = treasures, 711 unused ("rezerwa"), 712 = background. All 11
levels use 138, 205, 88, 0, 18.

### 1.2 Glyphs (charset.bin, 26 × 8 bytes at $9800, L25..L45, CHBAS=152 at L145)
`K` in the listing is the glyph number (screen code AND 63, L540..L545).

| glyph | ATASCII in DATA | meaning | K test |
|------:|-----------------|---------|--------|
| 0  | space   | empty | `K=0` |
| 1  | `!`     | poison carpet ("zatruty dywanik") | `ON K GOTO 505` |
| 2  | `"`     | trapdoor ("zapadnia") — the glyph is a **solid block identical to the wall** | `ON K GOTO 470` |
| 3  | `#`     | elevator ("winda") — solid block with two notches | `ON K GOTO 445` |
| 4..9 | inverse `$ % & ' ( )` | six treasure pictures, colour 2 | `P = K>3 AND K<10` |
| 10 | `*`     | wall, rounded right end | solid |
| 11 | `+`     | wall, rounded left end | solid |
| 12,13,14 | CHR$(12..14) | hero facing left / front / right, colour 1 | solid (never probed) |
| 15 | `/`     | wall block | solid |
| 16..25 | `0`..`9` | digits carved into a wall block (counters) | solid |

"Solid" = any `K>0` that is not a treasure and not 1..3 (L250, L260, L595, L615, L625).

### 1.3 Level data (levels.txt, from DATA 2000..4150)
Per level, consumed by READ in this order (L150..L175, L450):
5 colours; 12 rows of 20 characters; `X, Y` hero start; `LC` number of
treasures (the game counts **LC**, not the cells); `MC` time limit; `MK` step
limit; `NS` line of the next level (0 = last); then the **elevator list**,
read one value per elevator use (L450), terminated by 0.
11 levels: DATA lines 2000, 2200, …, 4000; `NS` chains them; level 11 has `NS=0`.

### 1.4 Variables
| BASIC | engine | meaning |
|-------|--------|---------|
| X, Y | `x, y` | hero cell |
| ZW | `zw` / `facing` | −1 left, 0 front, +1 right; also the horizontal velocity in the air |
| SK | `sk` | 0 on the ground, +1 rising, −1 falling |
| H | `h` | jump/fall counter (see §3) |
| MH | `MH` = 2 | maximum jump height (L180) |
| LC | `lc` | treasures left |
| MC, T | `mc, t` | time left, sub-counter 0..9 |
| MK | `mk` | steps left |
| KR, CZ | `Variant` | steps limited = Q>1, time limited = Q−2·KR (L140) |
| RE, NS | `level` | current level's DATA line |

---

## 2. Tick model

`tick(key)` = one pass of the main loop starting at L200, ending when the
program next reaches L200 (or dies / completes the level). There is no frame
timer in the original: the loop runs as fast as Atari BASIC interprets it,
about 10 passes per second **[EST]**, and that speed *is* the game speed (the
time limit counts passes, §5.1).

```
L205  draw hero CHR$(13+ZW) at (X,Y)                       [screen write]
L210  if time limited: T+=1; if T>=10: show MC; MC-=1; T=0; if MC<0 -> DEATH
L235  if LC=0 -> LEVEL DONE
L240  if HELP pressed -> MENU (level kept)
L245  (K,P) = terrain below (X,Y+1)
L250  if K=0 or P -> COLLISION            (nothing solid below: keep moving)
L255  SK=0; if H<0 -> DEATH               (landed after too long a fall)
L260  if K<4 -> COLLISION                 (standing on poison/trapdoor/elevator)
L265  sound off
L270  key = PEEK(764); no key -> next pass
L275  if steps limited: MK-=1; if MK<0 -> DEATH; show MK
L295  '+' : sound; if ZW=-1 -> COLLISION else ZW-=1
      '*' : sound; if ZW=+1 -> COLLISION else ZW+=1
      space: sound; SK=1; H=0 -> COLLISION
      anything else: ignored (but it cost a step)
```

**Keys are only read when the hero stands on solid ground** (L245..L260 leave
the pass before L270 otherwise). The OS keeps the last key in 764 until the
program clears it (L295), so a key pressed in mid-air is honoured on landing.
A `+` or `*` first *turns* the hero (ZW steps by one through −1, 0, +1) and
moves only when the hero already faces that way. A jump keeps the current ZW:
facing front (ZW=0) jumps straight up.

`COLLISION` (L555..L570):
```
L560  cursor = (X,Y)                       [remembered to erase the hero, L645]
L565  ON K GOTO poison(1), trapdoor(2), elevator(3)   -- K is whatever K holds:
                                              the terrain below, or a key code (6/7/33)
L570  if H>=0: X += ZW ;  Y -= SK
L575  (K,P) = terrain at the new (X,Y); if P: collect (K becomes 0, see §4)
L580  if SK=0 -> HORIZONTAL
L590  sound 77-10*H
L595  if K>0 -> DEATH                      (anything in the way while airborne)
L600  H += SK; if H=MH: SK=-1              (apex)
L645  erase the old cell -> next pass
```
`HORIZONTAL` (L610..L645):
```
L615  sound off; if K>0 -> DEATH           (walked into anything)
L620  (K,P) = terrain below the new cell
L625  if solid (K>0 and not P): erase old -> next pass
L630  sound 60
L635  if K>0 (a treasure below): collect
L640  Y += 1; H = MH-1; SK = -1            (step off an edge: drop one row, start falling)
L645  erase old -> next pass
```

---

## 3. Movement rules (consequences of §2)

### 3.1 Walking
Facing ZW and pressing that direction moves one cell. **The target cell must
be empty (or a treasure)**: walls, poison, trapdoors, elevators in the way are
all fatal (L615). If the cell below the target is solid the hero stands there;
if it is empty or a treasure the hero drops one row at once (L640) with `H=1,
SK=-1` and keeps *falling diagonally* (§3.3).

### 3.2 Jumping
Space sets `SK=1, H=0` and the first step is taken in the same pass:
`(X+ZW, Y-1)`, then `H=1`. Every following pass, while nothing solid is below,
moves `(X+ZW, Y-SK)`; at `H=MH=2` the direction flips to falling. Trajectory
facing right from (x,y): (x+1,y−1) (x+2,y−2) (x+3,y−1) (x+4,y) (x+5,y+1) …
then straight down (§3.3). Facing front: (x,y−1) (x,y−2) (x,y−1) (x,y).
Any non-empty cell entered in the air is fatal (L595): ceilings, and the
"corners of stones" the article warns about. A treasure entered in the air is
simply collected. If solid ground appears below after the first step (jumping
onto a one-row ledge) the jump ends there (L255).

### 3.3 Falling and the fall limit
While falling the hero keeps its horizontal velocity ZW **only while H≥0**
(L570), i.e. for the first two rows after stepping off an edge (H=1, then 0);
after that it falls straight down (H=−1, −2, …). Landing (something solid
below at the start of a pass) is fatal iff `H<0` (L255): a fall of more than
two rows kills, exactly as the article says ("upadek z wysokości większej od
dwukrotnego Twego wzrostu"). Landing counts the rows below the row the hero
stepped off from: 1 or 2 safe, 3 fatal.

### 3.4 Special floors (checked when standing, L260)
- **Poison carpet** (glyph 1): the hero glyph is flipped upside down 8 times
  (L510..L530; the double swap leaves the glyph unchanged) → death.
- **Trapdoor** (glyph 2): each pass while standing on it, `RND < 1-(LC-1)/20`
  keeps it shut (L475) and the keys are read normally (→ L265); otherwise the
  cell below becomes empty (L480), `ZW=0`, and the hero drops (L640). So with
  one treasure left a trapdoor never opens, with 21 it always does. Trapdoors
  look exactly like walls.
- **Elevator** (glyph 3): READ the next value WD of the level's list (L450);
  `WD=0` (or an exhausted list) behaves like the poison carpet. Else `ZW=0`,
  `X=INT(WD/10)`, `Y=WD-10*X+1` (L455) and the arrival is processed by
  L575..: a treasure at the destination is collected, a solid destination is
  fatal (L615), an empty cell below makes the hero drop. The list is consumed
  in order for the whole level attempt and reset by any reload of the level.

### 3.5 "I walked into a treasure and died"

Not into the treasure: into what is behind it. Entering **any** non-empty
cell is fatal in the original, on foot (L615, `IF K>0 THEN 650`) and in the
air (L595); a wall does not stop the hero, it kills it. A treasure is the one
exception, because the points routine (L360..L385) leaves `K=0` behind (the
loop `K=120 … K=K-40` three times). In level 1 the treasures sit right next
to the trunk of walls:

```
*       )/%        +      row 10
   … (11,10) -> (10,10): treasure collected
                 (9,10): wall '/' -> death
```

so after a treasure the hero has to stop or turn. It feels "too fast"
because of the key auto-repeat: the XL/XE OS repeats a held key every 6
frames (about 120 ms) after a delay of 48 frames (about 1 s), and the game
reads register 764 on every pass of its loop (L270) and clears it (L295), so
a held direction marches the hero straight into the wall. Add the buffer: a
key pressed in the air stays in 764 and is acted on after landing. The front
end reproduces both (`pygame.key.set_repeat(960, 120)`, the pending key kept
until L270 reads it). The article says it outright: *stawiaj kroki
rozważnie* ("place your steps carefully"), and the step-limited variant
counts every one of them (111 for level 1).

---

## 4. Treasures and the counters

`collect` = L360..L385: a 3-note jingle, `LC=LC-1`, and **K is left at 0**
(the loop `K=120 … K=K-40` three times), which is why a treasure is never
"in the way". Treasures are collected by entering their cell (L575, walking
or airborne) or by stepping onto the cell above them (L635, then the hero
drops into the cell). The screen cell is overwritten by the hero.

Time (L210..L230): only if CZ. `T` starts at 9 (L180), so the very first pass
of a level shows MC; every 10th pass shows MC then decrements it; when the
value shown was 0 the next decrement makes MC<0 → death. Display: `//` then the
number at column `3-(MC>9)-(MC>99)` of row 0 (right-aligned to column 5).

Steps (L275..L290): only if KR. **Every key press costs a step**, including
keys that do nothing, and the check `MK<0` → death happens before the move.
Display: `//` then MK at column `13-(MK>9)-(MK>99)` (right-aligned to 15);
at level start MK alone at `15-(MK>9)-(MK>99)` (L190).

---

## 5. Level flow

- Load (L145..L195): GRAPHICS 18, CHBAS=152, colours, 12 rows printed with a
  chirp each, `MH=2, ZW=0, SK=0, H=2, T=9`, MK shown if KR.
- Level done (L235 → L390): 36-note tune, each note flipping the screen
  upside down (CHACT toggled, L400); then clear; `NS=0` → the menu with `RE`
  reset to 2000 (L50), else the next level.
- Death (L650..L725): the hero glyph disintegrates (8×4 halvings/doublings of
  its bytes with sound), clear screen, glyphs 12..15 rewritten from DATA
  1070/1080 while 712 flashes, a fade-out, then **the same level again**. There
  are no lives.
- HELP (L240 → L55): back to the menu at any time; START then reloads the
  *current* level with the newly chosen variant (RE is kept, L140 → L145).
- OPTION in the menu quits to BASIC (L120).

## 6. Menu (L55..L140, front end)
GRAPHICS 0, lines at rows 3, 6..9, 13..15, 18, 20 (the cursor-down characters
in the strings), the cursor left at column 4 of row `Q+6` (L110) shows the
selected variant by inverting its digit. SELECT cycles Q 0→3→0 (L135), START
plays, OPTION ends. The console keys are polled without debounce (L115); the
front end takes one step per press.

## 7. Timing **[EST]**
Atari BASIC has no timer here; everything is statement-paced. Estimates used
by the front end, derived from the usual ~2 ms FOR/NEXT overhead and ~5 ms per
arithmetic statement of Atari BASIC (PCW benchmarks):
- one pass of the main loop with no key: ≈ 100 ms → **5 PAL frames per tick**;
- a FOR loop iteration containing one SOUND: ≈ 7 ms;
- a loop iteration with PEEK/POKE arithmetic and a SOUND (death): ≈ 30 ms.
These only affect how the game *feels* and the length of the jingles, not
any rule. Confirming them needs the original Atari BASIC ROM in an emulator
(open item, see README).

## 8. Engine API
```python
levels = parse_levels(open('levels.txt').read())         # list[Level]
e = Engine(levels, Variant.TIME_STEPS, start_level=0, rnd=random.Random(1))
events = e.tick(Key.LEFT)     # Key.NONE/LEFT/RIGHT/JUMP/OTHER/HELP
e.screen                      # 240 screen codes (glyph | colour<<6)
e.state                       # x y facing sk h treasures_left time_left steps_left t level status variant
e.load_pending; e.load_level()   # after DEATH / LEVEL_DONE, when the front end has shown the animation
e.restart_level(Variant.FREE)    # START after HELP (same level) or after FINISHED (level 1)
```
Events (`EventKind`): `LEVEL_LOADED, SOUND (f,d,v), SHOW_TIME, SHOW_STEPS,
TREASURE, ELEVATOR (x,y), TRAPDOOR (cell), POISON, DEATH, LEVEL_DONE, MENU`.
`SOUND` carries the literal `SOUND 1,f,d,v` arguments of single statements;
the multi-statement sound loops are named events and reproduced by stage 4.

## 9. Acceptance tests (tests/test_engine.py)
T1 turn/walk/erase; T2 walking into each solid kind is fatal; T3 stepping off
an edge, diagonal fall, 1–2 rows safe, 3 fatal; T4 jump arc, vertical jump,
ceiling, diagonal corner; T5 treasures collected in the three ways, never
fatal; T6 time counter and its display positions; T7 steps, any key, display;
T8 trapdoor probabilities and keys while shut; T9 elevator list order,
exhaustion, destination handling; T10 poison; T11 level chain and FINISHED;
T12 death restores the level (trapdoors, elevator list, counters); T13 HELP;
plus the 11 real levels (hero starts standing, sides closed) and a fuzz test
that random play never probes outside the screen.
