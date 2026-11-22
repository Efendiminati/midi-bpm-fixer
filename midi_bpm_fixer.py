#!/usr/bin/env python3
"""
MIDI BPM Fixer — change the BPM of a .mid file without moving the notes.

Two operations, because "change the BPM" means two different things:

  Re-grid (default)  The file starts stating the target BPM, and every tick is
                     rescaled so the notes still sound at the same moments.
                     This is what you want for MIDI exported by a transcriber
                     that stamps a fixed 120 BPM (Sonic Visualiser, Tony, most
                     AI transcription tools). Bars and beats finally line up.

  Re-time            Only the tempo event is rewritten. The notes keep their
                     tick positions, so the file plays faster or slower.
                     This is what you want for a practice pass.

The MIDI resolution (ticks per beat) is never touched. Note pitches, velocities,
channels, controllers and existing tempo/signature events are carried through.

MIT licensed. Requires: mido  (pip install mido)
"""

from __future__ import annotations

import os
import sys
import argparse
from pathlib import Path

import mido

APP_NAME = "MIDI BPM Fixer"
APP_VERSION = "1.0.0"
DEFAULT_TEMPO = 500000  # 120 BPM, the MIDI default when a file states no tempo

MODES = {
    "regrid": "Re-grid — keep timing, fix the bar grid",
    "retime": "Re-time — change the playback speed",
}

# ---------------------------------------------------------------- colours ---
BG        = "#0d0d0d"
PANEL     = "#141414"
FIELD     = "#1c1c1c"
FIELD_ALT = "#242424"
FG        = "#f2f2f2"
MUTED     = "#8a8a8a"
ACCENT    = "#ff7a18"
ACCENT_HI = "#ff9440"
ACCENT_LO = "#b85410"
DIM_TEXT  = "#d6d6d6"
LOG_BG    = "#0a0a0a"

FM = "Segoe UI"
FM_BOLD = ("Segoe UI", 10, "bold")
FM_MONO = ("Consolas", 9)


# ------------------------------------------------------------------ core ---
def _abs_ticks(track):
    """Yield (absolute_tick, message) for a track's delta-time messages."""
    tick = 0
    for msg in track:
        tick += msg.time
        yield tick, msg


def inspect(path):
    """Return (midi_file, source_tempo_us, distinct_tempos, n_tracks)."""
    mid = mido.MidiFile(str(path))
    tempos = []
    for track in mid.tracks:
        for _, msg in _abs_ticks(track):
            if msg.type == "set_tempo":
                tempos.append(msg.tempo)
    tempos.sort()
    src = tempos[0] if tempos else DEFAULT_TEMPO
    distinct = sorted(set(tempos))
    return mid, src, distinct, len(mid.tracks)


def set_time_signature(mid, numerator, denominator):
    """Write a time signature into track 0, replacing any existing one."""
    msg = mido.MetaMessage("time_signature",
                           numerator=numerator, denominator=denominator, time=0)
    track = mid.tracks[0]
    for i, existing in enumerate(track):
        if existing.type == "time_signature":
            msg.time = existing.time
            track[i] = msg
            return True
    track.insert(0, msg)
    return True


def convert(src_path, target_bpm, mode="regrid", time_sig=None):
    """Return (new_midi_file, info_dict). Never writes anything."""
    if mode not in MODES:
        raise ValueError("unknown mode: %s" % mode)
    if not (1.0 <= target_bpm <= 1000.0):
        raise ValueError("target BPM must be between 1 and 1000")

    mid, src_tempo, distinct, n_tracks = inspect(src_path)
    tgt_tempo = int(round(mido.bpm2tempo(target_bpm)))
    had_tempo = bool(distinct)

    out = mido.MidiFile(type=mid.type, ticks_per_beat=mid.ticks_per_beat)

    for track in mid.tracks:
        new_track = mido.MidiTrack()
        last = 0
        for tick, msg in _abs_ticks(track):
            new_msg = msg.copy()
            if new_msg.type == "set_tempo":
                new_msg.tempo = tgt_tempo
            if mode == "regrid":
                # new_tick = old_tick * source_tempo / target_tempo, rounded half up
                new_tick = (tick * src_tempo + tgt_tempo // 2) // tgt_tempo
            else:
                new_tick = tick
            new_msg.time = new_tick - last
            last = new_tick
            new_track.append(new_msg)
        out.tracks.append(new_track)

    if not had_tempo:
        # Nothing in the source stated a tempo, so write one or the file keeps
        # reading as 120 no matter what we did to the ticks.
        out.tracks[0].insert(0, mido.MetaMessage("set_tempo", tempo=tgt_tempo, time=0))

    if time_sig:
        set_time_signature(out, time_sig[0], time_sig[1])

    info = {
        "source_bpm": round(60_000_000 / src_tempo, 3),
        "source_tempo": src_tempo,
        "target_bpm": target_bpm,
        "target_tempo": tgt_tempo,
        "ppq": mid.ticks_per_beat,
        "tracks": n_tracks,
        "tempo_map": len(distinct) > 1,
        "had_tempo": had_tempo,
    }
    return out, info


def _bpm_tag(bpm):
    return ("%d" % bpm) if float(bpm).is_integer() else ("%g" % bpm)


def output_path(src_path, target_bpm, mode="regrid", outdir=None):
    """Pick a non-colliding output filename next to (or in outdir)."""
    src = Path(src_path)
    tag = "_bpm" + _bpm_tag(target_bpm)
    if mode == "retime":
        tag += "_retimed"
    suffix = src.suffix.lower() or ".mid"
    directory = Path(outdir) if outdir else src.parent
    candidate = directory / (src.stem + tag + suffix)
    n = 2
    while candidate.exists():
        candidate = directory / ("%s%s_%d%s" % (src.stem, tag, n, suffix))
        n += 1
    return candidate


def convert_file(src_path, target_bpm, mode="regrid", time_sig=None, outdir=None):
    """Convert one file and write the result. Returns (out_path, info)."""
    new_mid, info = convert(src_path, target_bpm, mode, time_sig)
    dest = output_path(src_path, target_bpm, mode, outdir)
    new_mid.save(str(dest))
    return dest, info


# ------------------------------------------------------------- verification ---
def _onsets_seconds(mid):
    """Absolute note-on times in real seconds, via mido's tempo-aware iterator.

    mido's playback iterator yields *delta* times in seconds, so they have to be
    accumulated to get absolute positions.
    """
    out = []
    clock = 0.0
    for msg in mid:                      # iterator honours set_tempo events
        clock += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            out.append(clock)
    return out


def selftest(verbose=True):
    """Build a synthetic 120 BPM file, re-grid it to 90, verify the timing."""
    import tempfile

    failures = []

    def check(name, ok, detail=""):
        if not ok:
            failures.append("%s %s" % (name, detail))
        if verbose:
            print("  [%s] %s %s" % ("ok" if ok else "FAIL", name, detail))

    # 3 notes starting at absolute ticks 0 / 480 / 960, each 240 ticks long,
    # PPQ 480, 120 BPM  =>  onsets at 0.0s / 0.5s / 1.0s
    ppq = 480
    mid = mido.MidiFile(type=1, ticks_per_beat=ppq)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    last = 0
    for abs_tick, kind in ((0, "on"), (240, "off"),
                           (480, "on"), (720, "off"),
                           (960, "on"), (1200, "off")):
        track.append(mido.Message(
            "note_on" if kind == "on" else "note_off", note=60,
            velocity=90 if kind == "on" else 0, time=abs_tick - last))
        last = abs_tick
    mid.tracks.append(track)

    tmpdir = Path(tempfile.mkdtemp(prefix="bpmfix-test-"))
    src = tmpdir / "test120.mid"
    mid.save(str(src))

    original = _onsets_seconds(mido.MidiFile(str(src)))
    check("fixture onsets", len(original) == 3, str(original))

    # --- re-grid to 90 -------------------------------------------------------
    dest, info = convert_file(src, 90, "regrid")
    fixed = mido.MidiFile(str(dest))
    regrid_onsets = _onsets_seconds(fixed)

    check("regrid: 3 notes preserved", len(regrid_onsets) == 3, str(regrid_onsets))
    drift = max(abs(a - b) for a, b in zip(original, regrid_onsets)) if regrid_onsets else 9e9
    check("regrid: onsets unchanged in seconds", drift < 0.002, "max drift %.5fs" % drift)

    tempos = [m.tempo for tr in fixed.tracks for m in tr if m.type == "set_tempo"]
    check("regrid: tempo event is 90 BPM", tempos and tempos[0] == mido.bpm2tempo(90),
          str(tempos))
    check("regrid: PPQ untouched", fixed.ticks_per_beat == ppq, str(fixed.ticks_per_beat))

    ticks = sorted(t for t, m in _abs_ticks(fixed.tracks[0])
                   if m.type == "note_on")
    check("regrid: ticks scaled to 0/360/720", ticks == [0, 360, 720], str(ticks))

    # --- re-time to 90 -------------------------------------------------------
    dest2, _ = convert_file(src, 90, "retime")
    retimed = mido.MidiFile(str(dest2))
    retime_onsets = _onsets_seconds(retimed)
    expected = [x * 120.0 / 90.0 for x in original]
    drift2 = max(abs(a - b) for a, b in zip(expected, retime_onsets)) if retime_onsets else 9e9
    check("retime: plays 4/3 slower", drift2 < 0.002,
          "%s vs %s" % ([round(x, 3) for x in retime_onsets],
                        [round(x, 3) for x in expected]))

    ticks2 = sorted(t for t, m in _abs_ticks(retimed.tracks[0]) if m.type == "note_on")
    check("retime: ticks untouched", ticks2 == [0, 480, 960], str(ticks2))

    # --- no-tempo file (a Sonic Visualiser export) ---------------------------
    notempo = mido.MidiFile(type=1, ticks_per_beat=ppq)
    tr2 = mido.MidiTrack()
    last = 0
    for abs_tick, kind in ((0, "on"), (240, "off"),
                           (480, "on"), (720, "off"),
                           (960, "on"), (1200, "off")):
        tr2.append(mido.Message(
            "note_on" if kind == "on" else "note_off", note=64,
            velocity=80 if kind == "on" else 0, time=abs_tick - last))
        last = abs_tick
    notempo.tracks.append(tr2)
    src3 = tmpdir / "sv_export.mid"
    notempo.save(str(src3))

    dest3, info3 = convert_file(src3, 100, "regrid")
    fixed3 = mido.MidiFile(str(dest3))
    tempos3 = [m.tempo for tr in fixed3.tracks for m in tr if m.type == "set_tempo"]
    check("sv: tempo event inserted", tempos3 == [mido.bpm2tempo(100)], str(tempos3))
    sv_onsets = _onsets_seconds(fixed3)
    check("sv: onsets unchanged (0/.5/1.0)",
          all(abs(a - b) < 0.002 for a, b in zip([0.0, 0.5, 1.0], sv_onsets)),
          str([round(x, 3) for x in sv_onsets]))
    check("sv: source BPM reported as 120", abs(info3["source_bpm"] - 120) < 0.01,
          str(info3["source_bpm"]))

    # --- time signature ------------------------------------------------------
    dest4, _ = convert_file(src, 90, "regrid", time_sig=(3, 4))
    fixed4 = mido.MidiFile(str(dest4))
    sigs = [(m.numerator, m.denominator, m.time)
            for tr in fixed4.tracks for m in tr if m.type == "time_signature"]
    check("time signature written", sigs and sigs[0][:2] == (3, 4), str(sigs))

    # --- output naming must never clobber ------------------------------------
    again, _ = convert_file(src, 90, "regrid")
    check("second run does not overwrite", again != dest and again.exists(), again.name)

    print("")
    if failures:
        print("FAILED (%d):" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("All checks passed. Artifacts in %s" % tmpdir)
    return 0


# ------------------------------------------------------------------- CLI ---
def cli(argv):
    p = argparse.ArgumentParser(
        prog="midi-bpm-fixer",
        description="Change a MIDI file's BPM without moving the notes.",
    )
    p.add_argument("files", nargs="*", help=".mid files, or directories to scan")
    p.add_argument("--bpm", type=float, help="target BPM")
    p.add_argument("--mode", choices=sorted(MODES), default="regrid",
                   help="regrid (default: keep timing) or retime (change speed)")
    p.add_argument("--time-sig", metavar="N/D", help="also write a time signature, e.g. 3/4")
    p.add_argument("-o", "--outdir", help="write results here instead of beside the input")
    p.add_argument("--gui", action="store_true", help="open the window (default when no --bpm)")
    p.add_argument("--selftest", action="store_true", help="run the built-in checks")
    p.add_argument("--version", action="version", version="%s %s" % (APP_NAME, APP_VERSION))
    args = p.parse_args(argv)

    if args.selftest:
        return selftest()

    if not args.bpm or not args.files:
        return run_gui(prefill=args.files)

    time_sig = None
    if args.time_sig:
        try:
            num, den = [int(x) for x in args.time_sig.split("/")]
        except ValueError:
            print("bad --time-sig, expected N/D like 3/4", file=sys.stderr)
            return 2
        time_sig = (num, den)

    targets = []
    for f in args.files:
        path = Path(f)
        if path.is_dir():
            targets += sorted(p for p in path.iterdir()
                              if p.suffix.lower() in (".mid", ".midi"))
        elif path.is_file():
            targets.append(path)
        else:
            print("not found: %s" % f, file=sys.stderr)

    failures = 0
    for path in targets:
        try:
            dest, info = convert_file(path, args.bpm, args.mode, time_sig, args.outdir)
            note = "  (tempo map: first tempo used)" if info["tempo_map"] else ""
            print("%s  %s -> %s BPM  (%s)%s"
                  % (path.name, _bpm_tag(info["source_bpm"]), _bpm_tag(args.bpm),
                     "re-gridded" if args.mode == "regrid" else "re-timed", note))
            print("   -> %s" % dest)
        except Exception as exc:                       # noqa: BLE001
            failures += 1
            print("%s: %s" % (path.name, exc), file=sys.stderr)

    return 1 if failures else 0


# ------------------------------------------------------------------- GUI ---
def run_gui(prefill=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox

    try:  # crisp text on Windows
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    root.title("%s %s" % (APP_NAME, APP_VERSION))
    root.configure(bg=BG)
    root.geometry("700x800")
    root.minsize(680, 620)

    state = {
        "files": [],
        "outdir": None,
        "busy": False,
    }

    def rule(parent, colour=ACCENT, height=2):
        f = tk.Frame(parent, bg=colour, height=height)
        f.pack(fill="x")
        return f

    def label(parent, text="", **kw):
        kw.setdefault("bg", BG)
        kw.setdefault("fg", FG)
        kw.setdefault("font", (FM, 10))
        return tk.Label(parent, text=text, **kw)

    def button(parent, text, command, primary=False, width=None):
        bg = ACCENT if primary else FIELD_ALT
        fg = "#121212" if primary else DIM_TEXT
        b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                      activebackground=ACCENT_HI if primary else "#2e2e2e",
                      activeforeground="#121212" if primary else FG,
                      relief="flat", bd=0, cursor="hand2",
                      font=(FM, 10, "bold") if primary else (FM, 9),
                      padx=14, pady=6)
        if width:
            b.configure(width=width)
        return b

    # ---- header
    head = tk.Frame(root, bg=BG)
    head.pack(fill="x", padx=20, pady=(18, 0))
    label(head, APP_NAME, bg=BG, fg=FG, font=(FM, 17, "bold")).pack(anchor="w")
    label(head, "Change a MIDI file's BPM without moving the notes",
          bg=BG, fg=MUTED, font=(FM, 9)).pack(anchor="w", pady=(2, 0))
    tk.Frame(root, bg=ACCENT, height=3).pack(fill="x", padx=20, pady=(12, 14))

    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=20)

    # ---- file row
    file_row = tk.Frame(body, bg=BG)
    file_row.pack(fill="x")
    file_var = tk.StringVar(value="No file selected")
    file_entry = tk.Entry(file_row, textvariable=file_var, state="readonly",
                          bg=FIELD, fg=DIM_TEXT, readonlybackground=FIELD,
                          relief="flat", bd=0, font=(FM, 9),
                          insertbackground=ACCENT)
    file_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
    button(file_row, "File…", lambda: pick_files(False)).pack(side="left", padx=(0, 6))
    button(file_row, "Folder…", lambda: pick_files(True)).pack(side="left")

    # ---- detected info
    info_var = tk.StringVar(value="")
    label(body, textvariable=info_var, bg=BG, fg=MUTED,
          font=(FM, 9)).pack(anchor="w", pady=(7, 0))

    # ---- BPM row
    bpm_row = tk.Frame(body, bg=BG)
    bpm_row.pack(fill="x", pady=(16, 0))
    label(bpm_row, "Target BPM", bg=BG, fg=FG,
          font=(FM, 10, "bold")).pack(side="left", padx=(0, 10))
    bpm_var = tk.StringVar(value="90")
    bpm_entry = tk.Entry(bpm_row, textvariable=bpm_var, bg=FIELD, fg=ACCENT,
                         relief="flat", bd=0, font=(FM, 14, "bold"), width=7,
                         justify="center", insertbackground=ACCENT)
    bpm_entry.pack(side="left", ipady=5)
    presets = (60, 75, 90, 100, 120, 128, 140)
    for value in presets:
        b = tk.Button(bpm_row, text=str(value), command=lambda v=value: bpm_var.set(str(v)),
                      bg=FIELD_ALT, fg=MUTED, activebackground=ACCENT_LO,
                      activeforeground="#111111", relief="flat", bd=0,
                      font=(FM, 8), padx=6, pady=4, cursor="hand2")
        b.pack(side="left", padx=(6, 0))

    # ---- mode
    mode_frame = tk.Frame(body, bg=PANEL)
    mode_frame.pack(fill="x", pady=(16, 0))
    label(mode_frame, "What to do", bg=PANEL, fg=ACCENT,
          font=(FM, 9, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
    mode_var = tk.StringVar(value="regrid")
    for key, text in MODES.items():
        desc = {
            "regrid": "Notes keep their exact timing; the file states the new BPM so "
                      "bars and beats line up. Use this for transcriber output that "
                      "says 120.",
            "retime": "Only the tempo changes, so the notes play faster or slower. "
                      "Use this for a slower practice pass.",
        }[key]
        row = tk.Frame(mode_frame, bg=PANEL)
        row.pack(fill="x", padx=12, pady=(0, 2))
        tk.Radiobutton(row, text=text, variable=mode_var, value=key,
                       bg=PANEL, fg=FG, selectcolor=FIELD, activebackground=PANEL,
                       activeforeground=ACCENT_HI, relief="flat", bd=0,
                       font=(FM, 10, "bold"), anchor="w",
                       highlightthickness=0, cursor="hand2").pack(anchor="w")
        label(mode_frame, desc, bg=PANEL, fg=MUTED, font=(FM, 8),
              wraplength=580, justify="left").pack(anchor="w", padx=34, pady=(0, 8))

    # ---- time signature
    ts_row = tk.Frame(body, bg=BG)
    ts_row.pack(fill="x", pady=(14, 0))
    ts_on = tk.BooleanVar(value=False)
    tk.Checkbutton(ts_row, text="Also set time signature", variable=ts_on,
                   bg=BG, fg=FG, selectcolor=FIELD, activebackground=BG,
                   activeforeground=ACCENT_HI, relief="flat", bd=0,
                   font=(FM, 9), highlightthickness=0,
                   cursor="hand2").pack(side="left")
    ts_num = tk.StringVar(value="4")
    ts_den = tk.StringVar(value="4")
    tk.Spinbox(ts_row, from_=1, to=16, width=3, textvariable=ts_num,
               bg=FIELD, fg=DIM_TEXT, buttonbackground=FIELD_ALT, relief="flat",
               bd=0, font=(FM, 9), justify="center").pack(side="left", padx=(10, 2))
    label(ts_row, "/", bg=BG, fg=MUTED).pack(side="left")
    tk.Spinbox(ts_row, values=(1, 2, 4, 8, 16), width=3, textvariable=ts_den,
               bg=FIELD, fg=DIM_TEXT, buttonbackground=FIELD_ALT, relief="flat",
               bd=0, font=(FM, 9), justify="center").pack(side="left", padx=(2, 0))
    ts_den.set("4")  # a Spinbox with `values` clamps its variable to the first entry
    label(body, "Sonic Visualiser leaves the time signature out entirely",
          bg=BG, fg=MUTED, font=(FM, 8)).pack(anchor="w", pady=(4, 0))

    # ---- action
    action = tk.Frame(body, bg=BG)
    action.pack(fill="x", pady=(18, 0))
    button(action, "CONVERT", lambda: start(), primary=True).pack(side="left")
    button(action, "Open output folder", lambda: open_folder()).pack(side="left", padx=(10, 0))

    # ---- log
    log = tk.Text(body, height=9, bg=LOG_BG, fg=DIM_TEXT, relief="flat", bd=0,
                  font=FM_MONO, wrap="word", padx=10, pady=8,
                  insertbackground=ACCENT, highlightthickness=0)
    log.pack(fill="both", expand=True, pady=(16, 4))
    log.tag_configure("ok", foreground=ACCENT_HI)
    log.tag_configure("err", foreground="#ff5555")
    log.tag_configure("dim", foreground=MUTED)
    log.configure(state="disabled")

    status = label(body, "", bg=BG, fg=ACCENT, font=(FM, 9))
    status.pack(anchor="w", pady=(0, 14))

    # ---- behaviour
    def write(text, tag=None):
        log.configure(state="normal")
        log.insert("end", text + "\n", tag)
        log.see("end")
        log.configure(state="disabled")

    def describe(path):
        try:
            mid, src, distinct, n_tracks = inspect(path)
        except Exception as exc:                       # noqa: BLE001
            info_var.set("Could not read this file: %s" % exc)
            return
        bpm = round(60_000_000 / src, 2)
        extra = " (tempo map — only the first tempo is used)" if len(distinct) > 1 else ""
        stated = "" if distinct else " (implied — the file states no tempo)"
        info_var.set("PPQ %d · %d track%s · current tempo %s BPM%s%s"
                     % (mid.ticks_per_beat, n_tracks, "" if n_tracks == 1 else "s",
                        _bpm_tag(bpm), stated, extra))

    def pick_files(folder):
        if folder:
            chosen = filedialog.askdirectory(title="Pick a folder of .mid files")
            if not chosen:
                return
            found = sorted(p for p in Path(chosen).iterdir()
                           if p.suffix.lower() in (".mid", ".midi"))
            if not found:
                messagebox.showinfo(APP_NAME, "No .mid files in that folder.")
                return
            state["files"] = found
            state["outdir"] = Path(chosen)
            file_var.set("%d MIDI files in %s" % (len(found), Path(chosen).name))
            info_var.set("Batch mode — each file gets its own converted copy")
            write("Found %d file(s) in %s" % (len(found), chosen), "dim")
        else:
            chosen = filedialog.askopenfilenames(
                title="Pick MIDI files",
                filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")])
            if not chosen:
                return
            state["files"] = [Path(p) for p in chosen]
            state["outdir"] = None
            if len(state["files"]) == 1:
                file_var.set(str(state["files"][0]))
                describe(state["files"][0])
            else:
                file_var.set("%d MIDI files selected" % len(state["files"]))
                info_var.set("Batch mode — each file gets its own converted copy")
            write("Selected: " + ", ".join(p.name for p in state["files"]), "dim")

    def open_folder():
        target = state["outdir"]
        if not target and state["files"]:
            target = state["files"][0].parent
        if target and Path(target).exists():
            try:
                os.startfile(str(target))               # noqa: S606
            except Exception as exc:                    # noqa: BLE001
                write("Could not open folder: %s" % exc, "err")
        else:
            write("No output folder yet.", "dim")

    def start(_event=None):
        if state["busy"]:
            return
        if not state["files"]:
            messagebox.showinfo(APP_NAME, "Pick a MIDI file or a folder first.")
            return
        try:
            target_bpm = float(str(bpm_var.get()).replace(",", "."))
            if not (1.0 <= target_bpm <= 1000.0):
                raise ValueError("BPM must be between 1 and 1000")
        except ValueError as exc:
            messagebox.showerror(APP_NAME, "That BPM doesn't work: %s" % exc)
            return

        time_sig = None
        if ts_on.get():
            try:
                num = int(ts_num.get())
                den = int(ts_den.get())
                if num < 1 or den < 1:
                    raise ValueError
                time_sig = (num, den)
            except ValueError:
                messagebox.showerror(APP_NAME, "Time signature must be whole numbers.")
                return

        state["busy"] = True
        status.configure(text="Working…")
        root.update_idletasks()

        mode = mode_var.get()
        done, failed = 0, 0
        for path in state["files"]:
            try:
                dest, info = convert_file(path, target_bpm, mode, time_sig,
                                          state["outdir"])
                note = "  [tempo map: first tempo used]" if info["tempo_map"] else ""
                write("%s  %s → %s BPM, %s%s"
                      % (path.name, _bpm_tag(info["source_bpm"]),
                         _bpm_tag(target_bpm),
                         "re-gridded" if mode == "regrid" else "re-timed", note),
                      "ok")
                write("    %s" % dest, "dim")
                done += 1
            except Exception as exc:                   # noqa: BLE001
                write("%s: %s" % (path.name, exc), "err")
                failed += 1

        state["busy"] = False
        status.configure(text="%d converted%s" % (
            done, (", %d failed" % failed) if failed else ""))
        if done == 1 and not failed:
            state["outdir"] = Path(state["files"][0]).parent

    bpm_entry.bind("<Return>", start)
    root.bind("<Return>", start)

    if prefill:
        existing = [Path(p) for p in prefill if Path(p).suffix.lower() in (".mid", ".midi")]
        if existing:
            state["files"] = existing
            file_var.set(str(existing[0]) if len(existing) == 1
                         else "%d MIDI files" % len(existing))
            if len(existing) == 1:
                describe(existing[0])

    write("Re-grid keeps the notes where they are and fixes the bar grid.", "dim")
    write("Re-time changes how fast the file plays.", "dim")

    # size the window to its own content so nothing is ever clipped
    root.update_idletasks()
    root.geometry("%dx%d" % (max(700, root.winfo_reqwidth()),
                             max(720, root.winfo_reqheight() + 16)))

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
